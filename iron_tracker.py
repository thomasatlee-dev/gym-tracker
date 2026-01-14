import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import time
import calendar
from datetime import datetime, date
from fpdf import FPDF
from streamlit_gsheets import GSheetsConnection

# ==========================================
# 1. SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="Iron Tracker", layout="wide", page_icon="🏋️")

# ESTABLISH CONNECTION TO GOOGLE SHEETS
conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def get_data():
    # Load data and ensure correct types (Sheets often reads as text)
    try:
        df = conn.read(worksheet="Sheet1", ttl=0) # ttl=0 means don't cache, reload fresh
        if df.empty:
            return pd.DataFrame(columns=['date', 'exercise', 'muscle_group', 'weight', 'reps', 'sets', 'sleep_hours', 'notes', 'estimated_1rm', 'volume'])
        
        # Force data types
        df['date'] = pd.to_datetime(df['date'])
        df['weight'] = pd.to_numeric(df['weight'], errors='coerce').fillna(0)
        df['reps'] = pd.to_numeric(df['reps'], errors='coerce').fillna(0)
        df['sets'] = pd.to_numeric(df['sets'], errors='coerce').fillna(0)
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        df['estimated_1rm'] = pd.to_numeric(df['estimated_1rm'], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Error reading Google Sheet: {e}")
        return pd.DataFrame()

def calculate_1rm(weight, reps):
    if reps == 1: return weight
    return weight * (1 + reps / 30)

def plate_calculator(target_weight, bar_weight):
    if target_weight <= bar_weight: return {}
    side_weight = (target_weight - bar_weight) / 2
    plates = [25, 20, 15, 10, 5, 2.5, 1.25]
    result = {}
    for p in plates:
        count = int(side_weight // p)
        if count > 0:
            result[p] = count
            side_weight -= (count * p)
    return result

def get_muscle_status(df):
    if df.empty: return pd.DataFrame()
    status = df.groupby('muscle_group')['date'].max().reset_index()
    status['days_since'] = (pd.to_datetime(date.today()) - status['date']).dt.days
    
    def get_color(days):
        if days <= 2: return "🔴 Recovering"
        if days <= 5: return "🟢 Prime State"
        return "🔵 Cold"
        
    status['Status'] = status['days_since'].apply(get_color)
    return status.sort_values('days_since')

def get_push_pull_ratio(df):
    push_exercises = [
        "Squat", "Leg Press", "Bench Press", "Incline Bench", 
        "Dips", "Overhead Press", "Lateral Raise", "Tricep Extension"
    ]
    def categorize(ex_name):
        return "Push" if ex_name in push_exercises else "Pull"
    
    df['type'] = df['exercise'].apply(categorize)
    return df.groupby('type')['volume'].sum().reset_index()

def plot_monthly_calendar(df, year, month):
    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)
    
    df['date'] = pd.to_datetime(df['date'])
    monthly_logs = df[(df['date'].dt.year == year) & (df['date'].dt.month == month)]
    active_days = monthly_logs['date'].dt.day.unique()
    
    x_data = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] * len(month_days)
    y_data = []
    z_data = []
    text_data = []
    
    for week_idx, week in enumerate(month_days):
        for day_num in week:
            y_data.append(f"Week {week_idx+1}")
            if day_num == 0:
                z_data.append(None)
                text_data.append("")
            else:
                z_data.append(1 if day_num in active_days else 0)
                text_data.append(str(day_num))
                
    fig = go.Figure(data=go.Heatmap(
        x=x_data, y=y_data, z=z_data, text=text_data,
        texttemplate="%{text}", textfont={"size":14, "color":"white"},
        colorscale=[[0, '#262730'], [1, '#00c853']], showscale=False,
        xgap=3, ygap=3
    ))
    fig.update_layout(
        title=f"{calendar.month_name[month]} {year}",
        yaxis=dict(autorange="reversed"), height=350,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    return fig

class PDFReport(FPDF):
    def header(self):
        self.set_fill_color(30, 30, 30)
        self.rect(0, 0, 210, 40, 'F')
        self.set_font('Helvetica', 'B', 20)
        self.set_text_color(255, 255, 255)
        self.cell(0, 15, 'IRON TRACKER // REPORT', ln=True, align='C')
        self.ln(10)

def create_pdf_report(df):
    pdf = PDFReport()
    pdf.add_page()
    pdf.set_font('Helvetica', '', 12)
    total_vol = df['volume'].sum()
    total_sessions = df['date'].nunique()
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, f"Total Lifetime Volume: {int(total_vol):,} kg", ln=True)
    pdf.cell(0, 10, f"Total Sessions Logged: {total_sessions}", ln=True)
    pdf.ln(10)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 10, "Est. 1RM Hall of Fame", ln=True)
    pdf.set_font('Helvetica', '', 12)
    key_lifts = ["Squat", "Bench Press", "Deadlift", "Overhead Press"]
    for lift in key_lifts:
        lift_data = df[df['exercise'] == lift]
        if not lift_data.empty:
            best = lift_data['estimated_1rm'].max()
            pdf.cell(0, 8, f"{lift}: {int(best)} kg", ln=True)
    return pdf.output(dest='S').encode('latin-1')

# ==========================================
# 4. SIDEBAR (INPUT)
# ==========================================
with st.sidebar:
    st.header("📝 Log Workout")
    st.caption("Connected to Google Sheets 🟢")

    with st.form("log_form"):
        date_in = st.date_input("Date", datetime.today())
        sleep_in = st.number_input("Sleep (Hrs)", 0.0, 14.0, 7.5, step=0.5)
        
        ex_map = {
            "Squat": "Legs", "Deadlift": "Back", "Leg Press": "Legs",
            "Bench Press": "Chest", "Incline Bench": "Chest", "Dips": "Chest",
            "Overhead Press": "Shoulders", "Lateral Raise": "Shoulders",
            "Barbell Row": "Back", "Pull Up": "Back", "Lat Pulldown": "Back",
            "Bicep Curl": "Arms", "Tricep Extension": "Arms"
        }
        exercise_in = st.selectbox("Exercise", list(ex_map.keys()))
        weight_in = st.number_input("Weight (kg)", 0.0, 500.0, 60.0, step=1.25)
        reps_in = st.number_input("Reps", 1, 100, 5)
        sets_in = st.number_input("Sets", 1, 20, 3)
        notes_in = st.text_input("Notes")
        
        submit_btn = st.form_submit_button("🔥 LOG SET")
        
        if submit_btn:
            e1rm = calculate_1rm(weight_in, reps_in)
            vol = weight_in * reps_in * sets_in
            muscle = ex_map[exercise_in]
            
            # CREATE NEW ROW
            new_data = pd.DataFrame([{
                "date": date_in.strftime("%Y-%m-%d"),
                "exercise": exercise_in,
                "muscle_group": muscle,
                "weight": weight_in,
                "reps": reps_in,
                "sets": sets_in,
                "sleep_hours": sleep_in,
                "notes": notes_in,
                "estimated_1rm": e1rm,
                "volume": vol
            }])
            
            # LOAD EXISTING, APPEND, AND SAVE
            try:
                current_df = get_data()
                updated_df = pd.concat([current_df, new_data], ignore_index=True)
                conn.update(worksheet="Sheet1", data=updated_df)
                st.success(f"Saved {exercise_in} to Google Sheets!")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

# ==========================================
# 5. MAIN APP
# ==========================================
tab1, tab2, tab3 = st.tabs(["📊 DASHBOARD", "🏋️ TOOLS", "📂 DATA"])

with tab1:
    df = get_data() # Gets fresh data from Google Sheets
    
    if not df.empty:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🗓️ Consistency")
            today = datetime.today()
            fig_cal = plot_monthly_calendar(df, today.year, today.month)
            st.plotly_chart(fig_cal, use_container_width=True)
            
        with col2:
            st.subheader("⚖️ Balance")
            pp_data = get_push_pull_ratio(df)
            if not pp_data.empty:
                fig_pp = px.pie(pp_data, values='volume', names='type', 
                                color='type', hole=0.4,
                                color_discrete_map={"Push": "#ff4b4b", "Pull": "#00c853"})
                st.plotly_chart(fig_pp, use_container_width=True)
                
        st.divider()
        st.subheader("📈 Strength Progress")
        ex_filter = st.selectbox("Select Lift", df['exercise'].unique())
        subset = df[df['exercise'] == ex_filter]
        
        if not subset.empty:
            subset = subset.sort_values(by="date")
            fig_prog = px.line(subset, x='date', y='estimated_1rm', markers=True, title=f"{ex_filter} 1RM Trend")
            st.plotly_chart(fig_prog, use_container_width=True)

        st.divider()
        st.subheader("🔋 Recovery Matrix")
        status_df = get_muscle_status(df)
        if not status_df.empty:
            fig_stat = px.bar(status_df, x='muscle_group', y='days_since', color='Status',
                              color_discrete_map={"🔴 Recovering": "#ff4b4b", "🟢 Prime State": "#00c853", "🔵 Cold": "#29b5e8"})
            st.plotly_chart(fig_stat, use_container_width=True)
    else:
        st.info("👈 Connect Google Sheet and log your first workout!")

with tab2:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("💿 Plate Calculator")
        bar = st.number_input("Bar Weight", value=20.0)
        target = st.number_input("Target Weight", value=100.0, step=2.5)
        if target > bar:
            plates = plate_calculator(target, bar)
            st.write(plates)
    with c2:
        st.subheader("⏱️ Rest Timer")
        if st.button("Start 3min Timer"):
            with st.empty():
                for i in range(180, 0, -1):
                    st.write(f"# {i//60}:{i%60:02d}")
                    time.sleep(1)
                st.write("# GO!")

with tab3:
    st.header("Manage Data")
    df_all = get_data()
    st.dataframe(df_all, use_container_width=True)
    
    # PDF Button
    if not df_all.empty:
        pdf_data = create_pdf_report(df_all)
        st.download_button("📄 Download PDF Report", data=pdf_data, file_name="gym_report.pdf", mime="application/pdf")
