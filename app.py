import streamlit as st
import streamlit.components.v1 as components
import PyPDF2
from fpdf import FPDF 
from PIL import Image
import base64
import json
import io
import os
import tempfile
import uuid
import re
from groq import Groq

# --- CONFIG ---
st.set_page_config(page_title="Harvard Live Resume Builder", layout="wide")

# --- SECURE API KEY ---
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError:
    st.error("⚠️ Groq API key not found in Streamlit Secrets!")
    st.stop()

client = Groq(api_key=GROQ_API_KEY)

# --- UTILITIES ---
def sanitize(text):
    if not text: return ""
    replacements = {'“': '"', '”': '"', "‘": "'", "’": "'", '–': '-', '—': '-', '•': '*'}
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def clean_url(url):
    return re.sub(r"^(https?://)?(www\.)?", "", url).rstrip("/")

def strip_internal_ids(data):
    if isinstance(data, dict):
        return {k: strip_internal_ids(v) for k, v in data.items() if k not in ['_id', 'photo_bytes']}
    elif isinstance(data, list):
        return [strip_internal_ids(v) for v in data]
    return data

# --- PDF ENGINE (HARVARD OCS STANDARDS) ---
class HarvardPDF(FPDF):
    def section_header(self, label, accent_rgb):
        self.set_font("Times", "B", 11)
        self.set_text_color(*accent_rgb)
        self.cell(0, 0.2, label.upper(), ln=1)
        self.set_draw_color(*accent_rgb)
        self.line(self.get_x(), self.get_y(), self.get_x() + 7.5, self.get_y())
        self.ln(0.1)
        self.set_text_color(0, 0, 0)

def generate_pdf(data, settings):
    pdf = HarvardPDF(unit="in", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    m = settings['margin']
    pw = 8.5
    uw = pw - (2 * m)
    accent = settings['accent_rgb']
    
    # Header
    pdf.set_font("Times", "B", settings['header_size'])
    pdf.cell(uw, 0.25, sanitize(data['name']).upper(), align='C', ln=1)
    
    pdf.set_font("Times", "", 10)
    contact = [data['address'], data['phone'], data['email'], clean_url(data['linkedin'])]
    pdf.cell(uw, 0.2, sanitize(" | ".join([p for p in contact if p])), align='C', ln=1)
    pdf.ln(0.15)

    # Sections
    for sec_key in settings['section_order']:
        # 1. Summary
        if sec_key == 'core_Summary' and data['summary']:
            pdf.section_header(data['heading_summary'], accent)
            pdf.set_font("Times", "", settings['font_size'])
            pdf.multi_cell(uw, 0.18 * settings['spacing'], sanitize(data['summary']))
            pdf.ln(0.1)

        # 2. Education
        elif sec_key == 'core_Education':
            pdf.section_header(data['heading_education'], accent)
            for ed in data['education']:
                if not ed.get('school'): continue
                pdf.set_font("Times", "B", settings['font_size'])
                pdf.cell(uw*0.75, 0.2, sanitize(ed['school']))
                pdf.cell(uw*0.25, 0.2, sanitize(ed['location']), align="R", ln=1)
                pdf.set_font("Times", "I", settings['font_size'])
                pdf.cell(uw*0.75, 0.2, sanitize(ed['degree']))
                pdf.cell(uw*0.25, 0.2, sanitize(ed['date']), align="R", ln=1)
                if ed.get('details'):
                    pdf.set_font("Times", "", settings['font_size'])
                    pdf.multi_cell(uw, 0.17, sanitize(ed['details']))
                pdf.ln(0.05)

        # 3. Experience / Leadership / Projects
        elif sec_key in ['core_Experience', 'core_Leadership', 'core_Projects']:
            key_map = {'core_Experience': ('experience', 'company'), 'core_Leadership': ('leadership', 'organization'), 'core_Projects': ('projects', 'title')}
            list_key, name_key = key_map[sec_key]
            
            pdf.section_header(data[f'heading_{list_key}'], accent)
            for item in data[list_key]:
                if not item.get(name_key): continue
                
                # Check for "Orphan" Prevention (estimate height)
                if pdf.get_y() > 9.5: pdf.add_page() 

                pdf.set_font("Times", "B", settings['font_size'])
                pdf.cell(uw*0.75, 0.2, sanitize(item[name_key]))
                pdf.cell(uw*0.25, 0.2, sanitize(item.get('location', '')), align="R", ln=1)
                
                pdf.set_font("Times", "I", settings['font_size'])
                pdf.cell(uw*0.75, 0.2, sanitize(item.get('title') or item.get('role', '')))
                pdf.cell(uw*0.25, 0.2, sanitize(item.get('date', '')), align="R", ln=1)
                
                pdf.set_font("Times", "", settings['font_size'])
                for bullet in item.get('bullets', '').split('\n'):
                    b = bullet.strip().lstrip('-').lstrip('•').strip()
                    if not b: continue
                    pdf.set_x(m + 0.15)
                    pdf.cell(0.1, 0.18, "*")
                    pdf.set_x(m + 0.3)
                    pdf.multi_cell(uw - 0.3, 0.18 * settings['spacing'], sanitize(b))
                pdf.ln(0.08)

        # 4. Skills
        elif sec_key == 'core_Skills':
            pdf.section_header(data['heading_skills'], accent)
            pdf.set_font("Times", "", settings['font_size'])
            sk = data['skills']
            parts = []
            if sk['technical']: parts.append(f"Technical: {sk['technical']}")
            if sk['languages']: parts.append(f"Languages: {sk['languages']}")
            if sk['interests']: parts.append(f"Interests: {sk['interests']}")
            pdf.multi_cell(uw, 0.18, sanitize(" | ".join(parts)))

    return pdf.output(dest='S'), pdf.page_no()

# --- INITIALIZATION ---
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': 'John Harvard', 'address': 'Cambridge, MA', 'phone': '617-555-0123', 'email': 'john@harvard.edu', 'linkedin': 'linkedin.com/in/johnh',
        'summary': '', 'heading_summary': 'Summary', 'heading_education': 'Education', 'heading_experience': 'Experience',
        'heading_projects': 'Projects', 'heading_leadership': 'Leadership', 'heading_skills': 'Skills & Interests',
        'education': [{'school': 'Harvard University', 'location': 'Cambridge, MA', 'degree': 'B.A. Computer Science', 'date': 'May 2025', 'details': 'GPA: 4.0'}],
        'experience': [{'company': 'Google', 'location': 'Mountain View, CA', 'title': 'Software Engineer Intern', 'date': 'Summer 2024', 'bullets': 'Improved latency by 20%.\nOptimized data pipelines.'}],
        'projects': [], 'leadership': [], 'skills': {'technical': 'Python, C++, SQL', 'languages': 'English, Spanish', 'interests': 'Chess, Rowing'},
        'custom_sections': []
    }
if 'section_order' not in st.session_state:
    st.session_state.section_order = ['core_Summary', 'core_Education', 'core_Experience', 'core_Projects', 'core_Leadership', 'core_Skills']

# --- UI LAYOUT ---
st.title("🚀 Real-Time Harvard Resume Builder")
st.caption("Edit on the left, see results instantly on the right. Accurate Harvard OCS Formatting.")

editor_col, preview_col = st.columns([1, 1])

# --- LEFT COLUMN: THE LIVE EDITOR ---
with editor_col:
    with st.expander("👤 Contact Information", expanded=True):
        st.session_state.r_data['name'] = st.text_input("Full Name", st.session_state.r_data['name'])
        c1, c2 = st.columns(2)
        st.session_state.r_data['email'] = c1.text_input("Email", st.session_state.r_data['email'])
        st.session_state.r_data['phone'] = c2.text_input("Phone", st.session_state.r_data['phone'])
    
    with st.expander("💼 Work Experience"):
        for i, exp in enumerate(st.session_state.r_data['experience']):
            st.markdown(f"**Job #{i+1}**")
            exp['company'] = st.text_input("Company", exp['company'], key=f"comp_{i}")
            exp['title'] = st.text_input("Title", exp['title'], key=f"tit_{i}")
            exp['date'] = st.text_input("Date (e.g. May 2021 - Present)", exp['date'], key=f"date_{i}")
            exp['bullets'] = st.text_area("Bullets (One per line)", exp['bullets'], key=f"bull_{i}", height=150)
            if st.button(f"🗑️ Delete Job {i+1}", key=f"del_{i}"):
                st.session_state.r_data['experience'].pop(i)
                st.rerun()
        if st.button("➕ Add Job"):
            st.session_state.r_data['experience'].append({'company': '', 'location': '', 'title': '', 'date': '', 'bullets': ''})
            st.rerun()

    with st.expander("🎓 Education"):
        for i, ed in enumerate(st.session_state.r_data['education']):
            ed['school'] = st.text_input("School", ed['school'], key=f"sch_{i}")
            ed['degree'] = st.text_input("Degree", ed['degree'], key=f"deg_{i}")
            ed['date'] = st.text_input("Date", ed['date'], key=f"edate_{i}")

    with st.expander("🛠️ Skills"):
        st.session_state.r_data['skills']['technical'] = st.text_area("Technical", st.session_state.r_data['skills']['technical'])
        st.session_state.r_data['skills']['interests'] = st.text_input("Interests", st.session_state.r_data['skills']['interests'])

    with st.expander("📏 Layout & Orphan Control"):
        spacing = st.slider("Line Spacing", 0.7, 1.5, 1.0, 0.05)
        margin = st.slider("Margins (in)", 0.4, 1.2, 0.75, 0.05)
        font_sz = st.slider("Font Size", 9, 12, 11)

# --- RIGHT COLUMN: INSTANT PREVIEW ---
with preview_col:
    settings = {
        'margin': margin, 'font_size': font_sz, 'header_size': 16, 
        'spacing': spacing, 'accent_rgb': (0,0,0), 
        'section_order': st.session_state.section_order, 'paper_size': 'Letter'
    }
    
    # Generate PDF in memory
    try:
        pdf_bytes, page_count = generate_pdf(st.session_state.r_data, settings)
        
        # Warning if overflowing to 2nd page unintentionally
        if page_count > 1:
            st.warning(f"⚠️ Resume is currently {page_count} pages. Adjust spacing or bullets to fit.")
        else:
            st.success("✅ Fits on 1 page.")

        # Display PDF
        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="1000px" type="application/pdf"></iframe>'
        st.markdown(pdf_display, unsafe_allow_html=True)
        
        st.download_button("⬇️ Download PDF", data=pdf_bytes, file_name="Resume.pdf", mime="application/pdf")
    except Exception as e:
        st.error(f"Preview Error: {e}")

# --- AI POLISH FLOATING BUTTON ---
if st.sidebar.button("✨ Auto-Polish with AI"):
    with st.spinner("AI is rewriting your bullets for impact..."):
        for exp in st.session_state.r_data['experience']:
            if exp['bullets']:
                prompt = f"Rewrite these resume bullets using the STAR method and strong action verbs: {exp['bullets']}"
                res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}])
                exp['bullets'] = res.choices[0].message.content
        st.rerun()
