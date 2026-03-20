import streamlit as st
import PyPDF2
from fpdf import FPDF 
import base64
import json
import uuid
import re
from groq import Groq

# --- SECURE API KEY HANDLING ---
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError:
    st.error("⚠️ Groq API key not found! Please add it to Streamlit Secrets.")
    st.stop()

client = Groq(api_key=GROQ_API_KEY)

# --- UTILITIES ---
def sanitize_text(text):
    if not text: return ""
    # Standardize quotes and dashes for Latin-1 (PDF standard)
    rep = {'“': '"', '”': '"', "‘": "'", "’": "'", '–': '-', '—': '-', '…': '...', '•': '-'}
    for k, v in rep.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def strip_internal(data):
    if isinstance(data, dict):
        return {k: strip_internal(v) for k, v in data.items() if k not in ['id']}
    elif isinstance(data, list):
        return [strip_internal(v) for v in data]
    return data

# --- AI ENGINE ---
def ai_parse_resume(text):
    prompt = f"Convert this resume text into a JSON object. Fields: name, address, phone, email, linkedin, summary, experience (list of: company, title, location, date, bullets), education (list of: school, degree, location, date, details), skills (technical, languages, interests). \n\nText: {text}"
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a JSON formatter."}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except: return None

# --- PDF ENGINE (THE "SMART" PART) ---
class HarvardPDF(FPDF):
    def section_header(self, title, font_fam, size):
        # If we are near the bottom (less than 1 inch), move header to next page
        if self.get_y() > (self.h - 1.5):
            self.add_page()
        self.ln(0.1)
        self.set_font(font_fam, "B", size)
        self.cell(0, 0.22, title.upper(), border="B", ln=True)
        self.ln(0.08)

def generate_resume(data, settings):
    pdf = HarvardPDF(unit="in", format="letter")
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    m = settings['margin']
    pdf.set_margins(left=m, top=m, right=m)
    f_fam = settings['font']
    base_sz = settings['font_size']
    
    # --- 1. HEADER ---
    pdf.set_font(f_fam, "B", 16)
    pdf.cell(0, 0.3, sanitize_text(data.get('name', 'Candidate')), align="C", ln=True)
    pdf.set_font(f_fam, "", base_sz)
    contact = [data.get('address',''), data.get('phone',''), data.get('email',''), data.get('linkedin','')]
    contact_str = " | ".join([c for c in contact if c])
    pdf.cell(0, 0.2, sanitize_text(contact_str), align="C", ln=True)

    # --- 2. SUMMARY ---
    if data.get('summary'):
        pdf.section_header("Professional Summary", f_fam, base_sz)
        pdf.set_font(f_fam, "", base_sz)
        pdf.multi_cell(0, 0.18, sanitize_text(data['summary']))

    # --- 3. EXPERIENCE ---
    if data.get('experience'):
        pdf.section_header("Experience", f_fam, base_sz)
        for job in data['experience']:
            # --- SMART OVERFLOW CHECK ---
            # Calculate approx height: Header(0.2) + Title(0.2) + 1st Bullet(0.2) + Gap
            # If current Y + 0.8 inches > Page Limit, push to next page
            needed = 0.8 + float(job.get('top_gap', 0))
            if (pdf.get_y() + needed) > (pdf.h - m) or job.get('force_page'):
                pdf.add_page()
            
            if job.get('top_gap'):
                pdf.ln(float(job['top_gap']))

            # Header Line
            pdf.set_font(f_fam, "B", base_sz)
            pdf.cell(pdf.epw*0.7, 0.18, sanitize_text(job.get('company','')))
            pdf.set_font(f_fam, "", base_sz)
            pdf.cell(pdf.epw*0.3, 0.18, sanitize_text(job.get('location','')), align="R", ln=True)
            
            # Title Line
            pdf.set_font(f_fam, "I", base_sz)
            pdf.cell(pdf.epw*0.7, 0.18, sanitize_text(job.get('title','')))
            pdf.set_font(f_fam, "", base_sz)
            pdf.cell(pdf.epw*0.3, 0.18, sanitize_text(job.get('date','')), align="R", ln=True)
            
            # Bullets
            pdf.set_font(f_fam, "", base_sz)
            bullets = job.get('bullets', '').split('\n')
            for b in bullets:
                if not b.strip(): continue
                pdf.set_x(m + 0.15)
                # Bullet Point (using standard dash for compatibility)
                pdf.cell(0.12, 0.18, "-")
                pdf.multi_cell(0, 0.18, sanitize_text(b.strip().lstrip('-•*')))
            pdf.ln(0.1)

    # --- 4. EDUCATION ---
    if data.get('education'):
        pdf.section_header("Education", f_fam, base_sz)
        for ed in data['education']:
            if (pdf.get_y() + 0.6) > (pdf.h - m): pdf.add_page()
            pdf.set_font(f_fam, "B", base_sz)
            pdf.cell(pdf.epw*0.7, 0.18, sanitize_text(ed.get('school','')))
            pdf.set_font(f_fam, "", base_sz)
            pdf.cell(pdf.epw*0.3, 0.18, sanitize_text(ed.get('date','')), align="R", ln=True)
            pdf.set_font(f_fam, "I", base_sz)
            pdf.multi_cell(0, 0.18, sanitize_text(ed.get('degree','')))
            if ed.get('details'):
                pdf.set_font(f_fam, "", base_sz - 1)
                pdf.multi_cell(0, 0.15, sanitize_text(ed['details']))
            pdf.ln(0.05)

    # --- 5. SKILLS ---
    pdf.section_header("Skills", f_fam, base_sz)
    sk = data.get('skills', {})
    pdf.set_font(f_fam, "", base_sz)
    if sk.get('technical'): 
        pdf.multi_cell(0, 0.18, sanitize_text(f"Technical: {sk['technical']}"), markdown=True)
    if sk.get('languages'): 
        pdf.multi_cell(0, 0.18, sanitize_text(f"Languages: {sk['languages']}"), markdown=True)
    if sk.get('interests'): 
        pdf.multi_cell(0, 0.18, sanitize_text(f"Interests: {sk['interests']}"), markdown=True)

    return pdf.output()

# --- STREAMLIT APP ---
st.set_page_config(page_title="Elite Builder", layout="wide")

if 'r_data' not in st.session_state:
    st.session_state.r_data = {'name': 'New Candidate', 'experience': [], 'education': [], 'skills': {}}

st.title("🎓 Harvard Resume Builder (Pro Edition)")
st.caption("Now featuring 'Smart Flow'—Job entries stay together and formatting is guaranteed.")

# --- AUTO-FILL SECTION ---
with st.expander("✨ Import Data (PDF or Text)", expanded=False):
    raw_text = st.text_area("Paste old resume text here:")
    if st.button("Magic Auto-Fill"):
        parsed = ai_parse_resume(raw_text)
        if parsed:
            st.session_state.r_data.update(parsed)
            st.rerun()

# --- THE EDITOR ---
col_edit, col_prev = st.columns([0.45, 0.55])

with col_edit:
    st.header("📝 Edit Content")
    tabs = st.tabs(["👤 Basics", "💼 Work", "🎓 Education", "🛠️ Skills"])
    
    with tabs[0]:
        d = st.session_state.r_data
        d['name'] = st.text_input("Full Name", d.get('name'))
        c1, c2 = st.columns(2)
        d['email'] = c1.text_input("Email", d.get('email'))
        d['phone'] = c2.text_input("Phone", d.get('phone'))
        d['address'] = st.text_input("Location (City, Country)", d.get('address'))
        d['linkedin'] = st.text_input("LinkedIn URL", d.get('linkedin'))
        d['summary'] = st.text_area("Summary", d.get('summary'), height=100)

    with tabs[1]:
        st.subheader("Work History")
        for i, job in enumerate(st.session_state.r_data.get('experience', [])):
            with st.expander(f"Job: {job.get('company', 'Empty')}", expanded=True):
                # Controls for layout
                c1, c2 = st.columns(2)
                job['force_page'] = c1.checkbox("📄 Start on new page", key=f"fp_{i}", value=job.get('force_page', False))
                job['top_gap'] = c2.slider("↕️ Top Gap (in)", 0.0, 1.0, float(job.get('top_gap', 0.0)), step=0.05, key=f"tg_{i}")
                
                job['company'] = st.text_input("Company", job.get('company'), key=f"co_{i}")
                job['title'] = st.text_input("Title", job.get('title'), key=f"ti_{i}")
                job['date'] = st.text_input("Dates (e.g. 2021 - Present)", job.get('date'), key=f"da_{i}")
                job['location'] = st.text_input("Location", job.get('location'), key=f"lo_{i}")
                job['bullets'] = st.text_area("Bullets (One per line)", job.get('bullets'), key=f"bu_{i}", height=120)
                if st.button("🗑️ Remove", key=f"rm_{i}"):
                    st.session_state.r_data['experience'].pop(i)
                    st.rerun()
        if st.button("➕ Add Work Experience"):
            st.session_state.r_data['experience'].append({'company': 'New Company', 'top_gap': 0.0})
            st.rerun()

    with tabs[2]:
        for i, ed in enumerate(st.session_state.r_data.get('education', [])):
            with st.expander(f"Edu: {ed.get('school', 'Empty')}"):
                ed['school'] = st.text_input("School", ed.get('school'), key=f"sc_{i}")
                ed['degree'] = st.text_input("Degree/Major", ed.get('degree'), key=f"de_{i}")
                ed['date'] = st.text_input("Dates", ed.get('date'), key=f"eda_{i}")
                ed['details'] = st.text_area("Honors/GPA/Details", ed.get('details'), key=f"edt_{i}")
        if st.button("➕ Add Education"):
            st.session_state.r_data['education'].append({'school': 'New University'})
            st.rerun()

    with tabs[3]:
        sk = st.session_state.r_data.get('skills', {})
        st.session_state.r_data['skills']['technical'] = st.text_area("Technical Skills", sk.get('technical'))
        st.session_state.r_data['skills']['languages'] = st.text_input("Languages", sk.get('languages'))
        st.session_state.r_data['skills']['interests'] = st.text_input("Interests", sk.get('interests'))

with col_prev:
    st.header("🖼️ Live Preview")
    
    settings = {
        'margin': 0.6,
        'font': 'Times',
        'font_size': 11
    }
    
    try:
        pdf_bytes = generate_resume(st.session_state.r_data, settings)
        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="900" type="application/pdf"></iframe>'
        st.markdown(pdf_display, unsafe_allow_html=True)
        
        st.download_button("📩 Download Final PDF", data=pdf_bytes, file_name="Resume_Pro.pdf", mime="application/pdf")
    except Exception as e:
        st.error(f"Formatting error: {e}")
        st.info("Tip: Check for unusual characters like emojis or special bullets.")
