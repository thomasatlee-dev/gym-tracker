import streamlit as st
import pandas as pd
import sqlalchemy
import plotly.express as px
import plotly.graph_objects as go
import time
import calendar
import io
from datetime import datetime, date
from fpdf import FPDF

# ==========================================
# 1. BACKEND & DATABASE SETUP
# ==========================================
engine = sqlalchemy.create_engine('sqlite:///iron_db.db')

def init_db():
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                exercise TEXT,
                muscle_group TEXT,
                weight FLOAT,
                reps INTEGER,
                sets INTEGER,
                sleep_hours FLOAT,
                notes TEXT,
                estimated_1rm FLOAT,
                volume FLOAT
            )
        """))
        conn.commit()

init_db()

# ==========================================
# 2. THE ALGORITHMS
# ==========================================

def calculate_1rm(weight, reps):
    if reps == 1: return weight
    return weight * (1 + reps / 30)

def plate_calculator(target_weight, bar_weight):
    if target_weight <= bar_weight:
        return {}
    
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
    status['days_since'] = (pd.to_datetime(date.today()) - pd.to_datetime(status['date'])).dt.days
    
    def get_color(days):
        if days <= 1: return "🔴 Recovering"
        if days <= 4: return "🟢 Prime State"
        return "🔵 Cold / Neglected"
        
    status['Status'] = status['days_since'].apply(get_color)
    return status.sort_values('days_since')

def get_push_pull_ratio(df):
    """Categorizes volume into Push vs Pull to detect imbalances."""
    push_exercises = [
        "Squat", "Leg Press", "Bench Press", "Incline Bench", 
        "Dips", "Overhead Press", "Lateral Raise", "Tricep Extension"
    ]
    
    def categorize(ex_name):
        return "Push" if ex_name in push_exercises else "Pull"
    
    df['type'] = df['exercise'].apply(categorize)
    ratio = df.groupby('type')['volume'].sum().reset_index()
    return ratio

def plot_monthly_calendar(df, year, month):
    """Creates a standard Monthly Calendar View."""
    cal = calendar.Calendar(firstweekday=0) # 0 = Monday
    month_days = cal.monthdayscalendar(year, month)
    
    df['date'] = pd.to_datetime(df['date'])
    monthly_logs = df[(df['date'].dt.year == year) & (df['date'].dt.month == month)]
    active_days = monthly_logs['date'].dt.day.unique()
    
    x_data = [] 
    y_data = [] 
    z_data = [] 
    text_data = [] 
    
    week_days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    
    for week_idx, week in enumerate(month_days):
        for day_idx, day_num in enumerate(week):
            x_data.append(week_days[day_idx])
            y_data.append(f"Week {week_idx+1}")
            
            if day_num == 0:
                text_data.append("")
                z_data.append(None)
            else:
                text_data.append(str(day_num))
                if day_num in active_days:
                    z_data.append(1)
                else:
                    z_data.append(0)
    
    fig = go.Figure(data=go.Heatmap(
        x=x_data,
        y=y_data,
        z=z_data,
        text=text_data,
        texttemplate="%{text}",
        textfont={"size": 14, "color": "white"},
        hoverinfo="skip",
        colorscale=[[0, '#262730'], [1, '#00c853']],
        showscale=False,
        xgap=3,
        ygap=3
    ))
    
    fig.update_layout(
        title=f"{calendar.month_name[month]} {year}",
        yaxis=dict(autorange="reversed"),
        height=350,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    return fig

# ==========================================
# 3. PDF GENERATION LOGIC
# ==========================================
class PDFReport(FPDF):
    def header(self):
        # Dark Header
        self.set_fill_color(30, 30, 30)
        self.rect(0, 0, 210, 40, 'F')
        self.set_font('Helvetica', 'B', 24)
        self.set_text_color(255, 255, 255) # White
        self.cell(0, 20, 'IRON TRACKER // TACTICAL REPORT', ln=True, align='C')
        self.set_font('Helvetica', 'I', 10)
        self.cell(0, 10, f'Generated: {date.today().strftime("%Y-%m-%d")}', ln=True, align='C')
        self.ln(10)

    def chapter_title(self, title):
        self.set_font('Helvetica', 'B', 16)
        self.set_text_color(0, 0, 0)
        self.cell(0, 10, title, ln=True, align='L')
        self.set_draw_color(0, 200, 83) # Green Line
        self.set_line_width(1)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def stat_row(self, label, value, is_good=True):
        self.set_font('Helvetica', '', 12)
        self.set_text_color(50, 50, 50)
        self.cell(100, 10, label, border=0)
        
        self.set_font('Helvetica', 'B', 12)
        if is_good:
            self.set_text_color(0, 150, 0) # Green
        else:
            self.set_text_color(200, 0, 0) # Red
            
        self.cell(0, 10, value, ln=True, border=0)

def create_pdf_report(df):
    pdf = PDFReport()
    pdf.add_page()
    
    # 1. CALCULATE DATES
    df['date'] = pd.to_datetime(df['date'])
    today = pd.to_datetime(date.today())
    last_30_start = today - pd.Timedelta(days=30)
    prev_30_start = last_30_start - pd.Timedelta(days=30)
    
    # 2. SPLIT DATA
    recent_df = df[(df['date'] >= last_30_start)]
    old_df = df[(df['date'] >= prev_30_start) & (df['date'] < last_30_start)]
    
    # 3. COMPUTE METRICS
    recent_vol = recent_df['volume'].sum()
    old_vol = old_df['volume'].sum()
    vol_diff = recent_vol - old_vol
    vol_pct = (vol_diff / old_vol * 100) if old_vol > 0 else 100
    
    recent_workouts = recent_df['date'].nunique()
    old_workouts = old_df['date'].nunique()
    
    # 4. THE "HARD TRUTH" VERDICT
    pdf.chapter_title("THE VERDICT")
    pdf.set_font('Helvetica', '', 12)
    pdf.set_text_color(0, 0, 0)
    
    verdict_text = ""
    verdict_color = (0, 0, 0)
    
    if vol_pct > 10 and recent_workouts >= 12:
        verdict_text = "OUTSTANDING. You have increased your workload significantly. The data shows you are showing up and putting in the work. Keep this momentum. Do not get comfortable."
        verdict_color = (0, 150, 0)
    elif vol_pct > 0:
        verdict_text = "ACCEPTABLE. You are maintaining progress, but the gains are marginal. You need to push the intensity in the next cycle. Comfort is the enemy of growth."
        verdict_color = (0, 0, 150)
    else:
        verdict_text = "UNSATISFACTORY. Your volume has dropped compared to the previous month. Numbers don't lie. You are either skipping sessions or lifting lighter. Fix it immediately."
        verdict_color = (200, 0, 0)
        
    pdf.set_text_color(*verdict_color)
    pdf.multi_cell(0, 8, verdict_text)
    pdf.ln(10)
    
    # 5. PERFORMANCE METRICS
    pdf.chapter_title("PERFORMANCE DELTA (Last 30 Days)")
    
    vol_str = f"{int(recent_vol):,} kg"
    if vol_diff > 0:
        vol_str += f" (+{int(vol_diff):,} kg) ^"
        is_good_vol = True
    else:
        vol_str += f" ({int(vol_diff):,} kg) v"
        is_good_vol = False
        
    pdf.stat_row("Total Volume Moved:", vol_str, is_good_vol)
    
    # Consistency
    cons_str = f"{recent_workouts} Sessions"
    if recent_workouts >= old_workouts:
        cons_str += f" (Prev: {old_workouts}) ^"
        is_good_cons = True
    else:
        cons_str += f" (Prev: {old_workouts}) v"
        is_good_cons = False
    
    pdf.stat_row("Consistency:", cons_str, is_good_cons)
    pdf.ln(10)
    
    # 6. HALL OF FAME (PRs)
    pdf.chapter_title("CURRENT 1RM ESTIMATES (HALL OF FAME)")
    pdf.set_font('Helvetica', '', 11)
    
    key_lifts = ["Squat", "Bench Press", "Deadlift", "Overhead Press"]
    
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 10, "LIFT", 1, 0, 'L', 1)
    pdf.cell(0, 10, "BEST ESTIMATED MAX", 1, 1, 'L', 1)
    
    pdf.set_text_color(0, 0, 0)
    for lift in key_lifts:
        lift_data = df[df['exercise'] == lift]
        if not lift_data.empty:
            best_1rm = lift_data['estimated_1rm'].max()
            pdf.cell(100, 10, lift, 1)
            pdf.cell(0, 10, f"{int(best_1rm)} kg", 1, 1)
        else:
            pdf.cell(100, 10, lift, 1)
            pdf.cell(0, 10, "No Data", 1, 1)
            
    pdf.ln(20)
    pdf.set_font('Helvetica', 'I', 10)
    pdf.cell(0, 10, "Print this. Tape it to your wall. Beat these numbers.", ln=True, align='C')
            
    return pdf.output(dest='S').encode('latin-1')

# ==========================================
# 4. FRONTEND UI
# ==========================================
st.set_page_config(page_title="Iron Tracker AU", layout="wide", page_icon="🦘")

# --- SIDEBAR ---
with st.sidebar:
    st.header("📝 Log Session (Metric)")
    with st.form("log_form"):
        date_in = st.date_input("Date", datetime.today())
        sleep_in = st.number_input("Hours Slept", 0.0, 14.0, 7.5, step=0.5)
        
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
        
        submit_btn = st.form_submit_button("💾 COMMIT TO DB")
        
        if submit_btn:
            e1rm = calculate_1rm(weight_in, reps_in)
            vol = weight_in * reps_in * sets_in
            muscle = ex_map[exercise_in]
            
            new_entry = pd.DataFrame([{
                'date': date_in, 'exercise': exercise_in, 'muscle_group': muscle,
                'weight': weight_in, 'reps': reps_in, 'sets': sets_in,
                'sleep_hours': sleep_in, 'notes': notes_in,
                'estimated_1rm': e1rm, 'volume': vol
            }])
            new_entry.to_sql('logs', engine, if_exists='append', index=False)
            st.success(f"Logged {exercise_in}: {weight_in}kg")

# --- MAIN TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["🏋️ THE GYM FLOOR", "📊 ANALYTICS HUB", "🧪 LAB", "🔧 SETTINGS"])

with tab1:
    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.subheader("💿 Smart Plate Calc")
        bar_input = st.number_input("Barbell Weight (kg)", value=20.0, step=1.0)
        target_w = st.number_input("Target Weight (kg)", value=100.0, step=2.5)
        needed = plate_calculator(target_w, bar_input)
        if needed:
            st.caption(f"Load per side (Bar: {bar_input}kg):")
            plate_visual = "|"
            for p, count in needed.items():
                for _ in range(count):
                    if p == 25: plate_visual += "[🔴]"
                    elif p == 20: plate_visual += "[🔵]"
                    elif p == 15: plate_visual += "[🟡]"
                    elif p == 10: plate_visual += "[🟢]"
                    else: plate_visual += f"[{p}]"
            st.code(plate_visual, language="text")
            for p, count in needed.items():
                st.write(f"**{p} kg** x {count}")
        else:
            st.warning("Weight too light.")

    with col_b:
        st.subheader("⏱️ Rest Timer")
        timer_mins = st.slider("Rest Minutes", 1, 5, 3)
        if st.button("Start Timer"):
            with st.empty():
                total_sec = timer_mins * 60
                for i in range(total_sec, 0, -1):
                    st.write(f"# ⏳ {i // 60}:{i % 60:02d}")
                    time.sleep(1)
                st.write("# 🔔 GO TIME!")

with tab2:
    df = pd.read_sql("SELECT * FROM logs", engine)
    
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        
        # --- ROW 1: CALENDAR & IMBALANCE ---
        col_c1, col_c2 = st.columns(2)
        
        with col_c1:
            st.subheader("📅 Training Calendar")
            today = datetime.today()
            col_sel1, col_sel2 = st.columns(2)
            with col_sel1:
                sel_year = st.selectbox("Year", range(2023, 2030), index=today.year-2023)
            with col_sel2:
                sel_month = st.selectbox("Month", range(1, 13), index=today.month-1)
            
            fig_cal = plot_monthly_calendar(df, sel_year, sel_month)
            st.plotly_chart(fig_cal, use_container_width=True)
            
        with col_c2:
            st.subheader("⚖️ Push / Pull Balance")
            pp_data = get_push_pull_ratio(df)
            
            if not pp_data.empty:
                fig_pp = px.pie(pp_data, values='volume', names='type', 
                                color='type',
                                color_discrete_map={"Push": "#ff4b4b", "Pull": "#00c853"},
                                hole=0.4)
                st.plotly_chart(fig_pp, use_container_width=True)
                
                push_vol = pp_data.loc[pp_data['type']=='Push', 'volume'].sum() if 'Push' in pp_data['type'].values else 0
                pull_vol = pp_data.loc[pp_data['type']=='Pull', 'volume'].sum() if 'Pull' in pp_data['type'].values else 0
                
                if pull_vol > 0:
                    ratio = push_vol / pull_vol
                    if ratio > 1.5:
                        st.warning(f"⚠️ Push Dominant (Ratio {ratio:.1f}). Add more Back/Hamstring work.")
                    elif ratio < 0.75:
                        st.info(f"ℹ️ Pull Dominant. Good for posture.")
                    else:
                        st.success("✅ Healthy 1:1 Structural Balance.")
            else:
                st.info("Log workouts to see balance data.")
        
        st.divider()
        
        # 2. Muscle Recovery Matrix
        st.subheader("🔋 Muscle Recovery Status")
        status_df = get_muscle_status(df)
        if not status_df.empty:
            fig_status = px.bar(status_df, x='muscle_group', y='days_since', color='Status',
                                color_discrete_map={"🔴 Recovering": "#ff4b4b", "🟢 Prime State": "#00c853", "🔵 Cold / Neglected": "#29b5e8"},
                                title="Days Since Last Workout")
            st.plotly_chart(fig_status, use_container_width=True)
        
        st.divider()
        
        # 3. Strength Tracker
        st.subheader("📈 Strength Tracker (Daily Max)")
        ex_filter = st.selectbox("Select Lift", df['exercise'].unique())
        
        subset = df[df['exercise'] == ex_filter]
        daily_max = subset.groupby('date')['estimated_1rm'].max().reset_index()
        
        fig_prog = px.line(daily_max, x='date', y='estimated_1rm', markers=True,
                           title=f"Best Daily Effort: {ex_filter} (kg)")
        st.plotly_chart(fig_prog, use_container_width=True)
        
        current_pr = daily_max['estimated_1rm'].max()
        st.metric(label=f"All-Time Best {ex_filter} (Est. 1RM)", value=f"{int(current_pr)} kg")

    else:
        st.info("Log your first workout in the sidebar.")

with tab3:
    st.header("🧪 The Sleep/Strength Correlation")
    if not df.empty and len(df) > 5:
        corr_ex = st.selectbox("Correlate Sleep for:", df['exercise'].unique(), key="corr_select")
        corr_df = df[df['exercise'] == corr_ex]
        
        corr_grouped = corr_df.groupby('date').agg({'sleep_hours':'mean', 'estimated_1rm':'max'}).reset_index()
        
        fig_corr = px.scatter(corr_grouped, x="sleep_hours", y="estimated_1rm", 
                              trendline="ols",
                              title=f"Does Sleep Impact Your {corr_ex}?")
        st.plotly_chart(fig_corr, use_container_width=True)
    else:
        st.warning("Need more data points (at least 5 logs) to run correlation algorithms.")

with tab4:
    st.header("🔧 Data Management & Reports")
    
    df_all = pd.read_sql("SELECT * FROM logs ORDER BY date DESC", engine)
    
    col_rep, col_data = st.columns([1, 2])
    
    with col_rep:
        st.subheader("🖨️ Tactical Report")
        st.write("Generate a PDF summary of your last 30 days vs. the previous period.")
        
        if st.button("Generate PDF Report"):
            if not df_all.empty:
                pdf_bytes = create_pdf_report(df_all)
                st.download_button(
                    label="📄 Download PDF Dossier",
                    data=pdf_bytes,
                    file_name=f"Iron_Report_{date.today()}.pdf",
                    mime="application/pdf"
                )
            else:
                st.error("Not enough data to generate report.")

    with col_data:
        st.subheader("📝 Recent Logs")
        st.dataframe(df_all, use_container_width=True, height=300)
    
    st.divider()
    
    col_d1, col_d2 = st.columns(2)
    
    # 2. Delete Last Entry
    with col_d1:
        st.warning("⚠️ Danger Zone")
        if st.button("🗑️ Delete Last Log Entry"):
            with engine.connect() as conn:
                last_id_result = conn.execute(sqlalchemy.text("SELECT MAX(id) FROM logs")).scalar()
                if last_id_result:
                    conn.execute(sqlalchemy.text(f"DELETE FROM logs WHERE id = {last_id_result}"))
                    conn.commit()
                    st.success(f"Deleted Log ID: {last_id_result}")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Database is empty.")

    # 3. Export Data
    with col_d2:
        st.info("💾 Backup Raw Data")
        if not df_all.empty:
            csv = df_all.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"workout_logs_{date.today()}.csv",
                mime="text/csv",
            )