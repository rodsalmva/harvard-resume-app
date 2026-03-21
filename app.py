import streamlit as st
import streamlit.components.v1 as components
import PyPDF2
from fpdf import FPDF 
import base64
import json
import io
import os
import re
import uuid
from groq import Groq

# --- INITIAL SETUP ---
st.set_page_config(page_title="Harvard Resume Builder Pro", layout="wide")

try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError:
    st.error("⚠️ Groq API key not found! Add it to Streamlit Secrets.")
    st.stop()

client = Groq(api_key=GROQ_API_KEY)

# --- UTILITIES & SANITIZATION ---
def sanitize(text):
    if not text: return ""
    # Map common non-latin-1 characters to safe versions
    rep = {'“': '"', '”': '"', "‘": "'", "’": "'", '–': '-', '—': '-', '•': '*', '…': '...'}
    for k, v in rep.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def clean_url(url):
    return re.sub(r"^(https?://)?(www\.)?", "", url or "").rstrip("/")

def strip_internal_ids(data):
    if isinstance(data, dict):
        return {k: strip_internal_ids(v) for k, v in data.items() if k not in ['_id', 'photo_bytes']}
    elif isinstance(data, list):
        return [strip_internal_ids(v) for v in data]
    return data

# --- AI LOGIC ---
def polish_bullet_ai(text):
    prompt = f"Rewrite these resume bullets using the STAR method (Action Verb + Task + Result). Keep it punchy and professional:\n\n{text}"
    try:
        res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}])
        return res.choices[0].message.content.strip()
    except:
        return text

# --- PDF GENERATION ENGINE ---
class HarvardPDF(FPDF):
    def header(self): pass
    def footer(self): pass

def generate_harvard_pdf(data, settings):
    pdf = HarvardPDF(unit="in", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    m = settings['margin']
    uw = 8.5 - (2 * m)
    accent = settings['accent_rgb']
    f_size = settings['font_size']
    spacing = settings['spacing']

    # 1. HEADER
    pdf.set_font("Times", "B", 16)
    pdf.cell(uw, 0.25, sanitize(data['name']).upper(), align='C', ln=1)
    
    pdf.set_font("Times", "", 10)
    contact = [data.get('address'), data.get('phone'), data.get('email'), clean_url(data.get('linkedin'))]
    pdf.cell(uw, 0.2, sanitize(" | ".join([p for p in contact if p])), align='C', ln=1)
    pdf.ln(0.15)

    # 2. SECTIONS
    for sec_key in settings['section_order']:
        # Fetching titles and data blocks
        if sec_key == 'core_Summary' and data['summary']:
            pdf.set_font("Times", "B", f_size)
            pdf.cell(uw, 0.2, data['heading_summary'].upper(), ln=1)
            pdf.line(m, pdf.get_y(), 8.5-m, pdf.get_y())
            pdf.ln(0.05)
            pdf.set_font("Times", "", f_size)
            pdf.multi_cell(uw, 0.18 * spacing, sanitize(data['summary']))
            pdf.ln(0.1)

        elif sec_key == 'core_Education':
            pdf.set_font("Times", "B", f_size)
            pdf.cell(uw, 0.2, data['heading_education'].upper(), ln=1)
            pdf.line(m, pdf.get_y(), 8.5-m, pdf.get_y())
            pdf.ln(0.05)
            for ed in data['education']:
                if not ed.get('school'): continue
                if ed.get('force_page_break'): pdf.add_page()
                pdf.set_font("Times", "B", f_size)
                pdf.cell(uw*0.7, 0.18, sanitize(ed['school']))
                pdf.cell(uw*0.3, 0.18, sanitize(ed.get('location', '')), align="R", ln=1)
                pdf.set_font("Times", "I", f_size)
                pdf.cell(uw*0.7, 0.18, sanitize(ed.get('degree', '')))
                pdf.cell(uw*0.3, 0.18, sanitize(ed.get('date', '')), align="R", ln=1)
                if ed.get('details'):
                    pdf.set_font("Times", "", f_size)
                    pdf.multi_cell(uw, 0.18, sanitize(ed['details']))
                pdf.ln(0.05)

        elif sec_key in ['core_Experience', 'core_Leadership', 'core_Projects']:
            map_key = 'experience' if 'Experience' in sec_key else ('leadership' if 'Leadership' in sec_key else 'projects')
            label = data[f'heading_{map_key}'].upper()
            
            pdf.set_font("Times", "B", f_size)
            pdf.cell(uw, 0.2, label, ln=1)
            pdf.line(m, pdf.get_y(), 8.5-m, pdf.get_y())
            pdf.ln(0.05)
            
            for item in data[map_key]:
                name = item.get('company') or item.get('organization') or item.get('title')
                if not name: continue
                if item.get('force_page_break'): pdf.add_page()
                
                pdf.set_font("Times", "B", f_size)
                pdf.cell(uw*0.7, 0.18, sanitize(name))
                pdf.cell(uw*0.3, 0.18, sanitize(item.get('location', '')), align="R", ln=1)
                
                pdf.set_font("Times", "I", f_size)
                pdf.cell(uw*0.7, 0.18, sanitize(item.get('title') or item.get('role', '')))
                pdf.cell(uw*0.3, 0.18, sanitize(item.get('date', '')), align="R", ln=1)
                
                pdf.set_font("Times", "", f_size)
                bullets = item.get('bullets', '').split('\n')
                for b in bullets:
                    clean_b = b.strip().lstrip('-').lstrip('*').lstrip('•').strip()
                    if not clean_b: continue
                    pdf.set_x(m + 0.15)
                    pdf.cell(0.1, 0.18, chr(149))
                    pdf.set_x(m + 0.3)
                    pdf.multi_cell(uw - 0.3, 0.18 * spacing, sanitize(clean_b))
                pdf.ln(0.05)

        elif sec_key == 'core_Skills':
            pdf.set_font("Times", "B", f_size)
            pdf.cell(uw, 0.2, data['heading_skills'].upper(), ln=1)
            pdf.line(m, pdf.get_y(), 8.5-m, pdf.get_y())
            pdf.ln(0.05)
            sk = data['skills']
            pdf.set_font("Times", "", f_size)
            lines = []
            if sk.get('technical'): lines.append(f"**Technical Skills**: {sk['technical']}")
            if sk.get('languages'): lines.append(f"**Languages**: {sk['languages']}")
            if sk.get('interests'): lines.append(f"**Interests**: {sk['interests']}")
            pdf.multi_cell(uw, 0.18 * spacing, sanitize("  \n".join(lines)), markdown=True)

    return pdf.output(dest='S'), pdf.page_no()

# --- STATE MANAGEMENT ---
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': 'Candidate Name', 'address': 'City, State', 'phone': '555-555-5555', 'email': 'email@example.com', 'linkedin': 'linkedin.com/in/user',
        'summary': '', 'heading_summary': 'Summary', 'heading_education': 'Education', 'heading_experience': 'Experience',
        'heading_projects': 'Projects', 'heading_leadership': 'Leadership', 'heading_skills': 'Skills & Interests',
        'education': [{'school': '', 'location': '', 'degree': '', 'date': '', 'details': '', 'force_page_break': False}],
        'experience': [{'company': '', 'location': '', 'title': '', 'date': '', 'bullets': '', 'force_page_break': False}],
        'projects': [], 'leadership': [], 'skills': {'technical': '', 'languages': '', 'interests': ''}, 'custom_sections': []
    }
if 'section_order' not in st.session_state:
    st.session_state.section_order = ['core_Summary', 'core_Education', 'core_Experience', 'core_Projects', 'core_Leadership', 'core_Skills']

# --- UI LAYOUT ---
st.title("🎓 Harvard Resume Maker (Live Preview)")
editor_col, preview_col = st.columns([1, 1])

with editor_col:
    tabs = st.tabs(["👤 Info", "🎓 Edu", "💼 Exp", "🛠️ Skills", "⚙️ Layout"])
    
    with tabs[0]:
        st.session_state.r_data['name'] = st.text_input("Name", st.session_state.r_data['name'])
        c1, c2 = st.columns(2)
        st.session_state.r_data['email'] = c1.text_input("Email", st.session_state.r_data['email'])
        st.session_state.r_data['phone'] = c2.text_input("Phone", st.session_state.r_data['phone'])
        st.session_state.r_data['address'] = c1.text_input("Location", st.session_state.r_data['address'])
        st.session_state.r_data['linkedin'] = c2.text_input("LinkedIn", st.session_state.r_data['linkedin'])
        st.session_state.r_data['summary'] = st.text_area("Summary", st.session_state.r_data['summary'], height=100)

    with tabs[1]:
        for i, ed in enumerate(st.session_state.r_data['education']):
            with st.expander(f"School: {ed.get('school') or 'New'}", expanded=True):
                ed['school'] = st.text_input("School", ed['school'], key=f"ed_s_{i}")
                ed['degree'] = st.text_input("Degree", ed['degree'], key=f"ed_d_{i}")
                ed['date'] = st.text_input("Date", ed['date'], key=f"ed_dt_{i}")
                ed['force_page_break'] = st.checkbox("Push to Next Page", ed['force_page_break'], key=f"ed_pb_{i}")
        if st.button("➕ Add Education"):
            st.session_state.r_data['education'].append({'school': '', 'location': '', 'degree': '', 'date': '', 'details': '', 'force_page_break': False})
            st.rerun()

    with tabs[2]:
        for i, exp in enumerate(st.session_state.r_data['experience']):
            with st.expander(f"Job: {exp.get('company') or 'New'}", expanded=True):
                exp['company'] = st.text_input("Company", exp['company'], key=f"ex_c_{i}")
                exp['title'] = st.text_input("Title", exp['title'], key=f"ex_t_{i}")
                exp['date'] = st.text_input("Date", exp['date'], key=f"ex_dt_{i}")
                exp['bullets'] = st.text_area("Bullets", exp['bullets'], key=f"ex_b_{i}", height=120)
                exp['force_page_break'] = st.checkbox("Push to Next Page", exp['force_page_break'], key=f"ex_pb_{i}")
                if st.button("✨ Polish (AI)", key=f"ai_{i}"):
                    exp['bullets'] = polish_bullet_ai(exp['bullets'])
                    st.rerun()
        if st.button("➕ Add Job"):
            st.session_state.r_data['experience'].append({'company': '', 'location': '', 'title': '', 'date': '', 'bullets': '', 'force_page_break': False})
            st.rerun()

    with tabs[3]:
        sk = st.session_state.r_data['skills']
        sk['technical'] = st.text_area("Technical Skills", sk['technical'])
        sk['languages'] = st.text_input("Languages", sk['languages'])
        sk['interests'] = st.text_input("Interests", sk['interests'])

    with tabs[4]:
        margin = st.slider("Margins (in)", 0.4, 1.0, 0.75, 0.05)
        font_size = st.slider("Font Size", 9, 12, 11)
        spacing = st.slider("Line Spacing", 0.8, 1.2, 1.0, 0.05)

# --- PREVIEW RENDER ---
with preview_col:
    settings = {
        'margin': margin, 'font_size': font_size, 'spacing': spacing,
        'accent_rgb': (0,0,0), 'section_order': st.session_state.section_order
    }
    
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    
    if pages > 1:
        st.warning(f"Note: Your resume is {pages} pages long.")
    
    # PDF to Base64 for Browser Preview
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="900px" type="application/pdf"></iframe>'
    st.markdown(pdf_display, unsafe_allow_html=True)
    
    st.download_button("⬇️ Download PDF", data=pdf_bytes, file_name="Harvard_Resume.pdf", mime="application/pdf")

# Sidebar Export/Import
with st.sidebar:
    st.header("💾 Data Management")
    st.download_button("Download JSON Data", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume.json")
    up = st.file_uploader("Upload JSON Data")
    if up:
        st.session_state.r_data = json.load(up)
        st.rerun()
