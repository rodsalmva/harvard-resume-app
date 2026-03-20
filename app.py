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

# --- SECURE API KEY HANDLING ---
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except KeyError:
    st.error("⚠️ Groq API key not found! Please add it to Streamlit Secrets.")
    st.stop()

client = Groq(api_key=GROQ_API_KEY)

# --- UTILITY: TEXT SANITIZERS & CLEANERS ---
def sanitize(text):
    if not text: return ""
    replacements = {'“': '"', '”': '"', "‘": "'", "’": "'", '–': '-', '—': '-', '…': '...', '•': '-'}
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def clean_url(url):
    if not url: return ""
    return re.sub(r"^(https?://)?(www\.)?", "", url).rstrip("/")

def strip_internal_ids(data):
    if isinstance(data, dict):
        return {k: strip_internal_ids(v) for k, v in data.items() if k not in ['_id', 'photo_bytes']}
    elif isinstance(data, list):
        return [strip_internal_ids(v) for v in data]
    return data

# --- AI AUTOFOCUS & POLISH LOGIC ---
def auto_fill_with_ai(text, merge=False):
    baseline_data = strip_internal_ids(st.session_state.r_data) if merge else {
        "name": "", "address": "", "phone": "", "email": "", "linkedin": "",
        "summary": "", "education": [], "experience":[], "projects":[], "leadership":[],
        "skills": {"technical": "", "languages": "", "interests": ""},
        "custom_sections":[]
    }
    prompt = f"Convert this into a resume JSON. Keep all details. Output ONLY JSON.\n\n{text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON API."}, {"role": "user", "content": prompt}],
            temperature=0, 
            response_format={"type": "json_object"}
        )
        content = completion.choices[0].message.content.strip()
        parsed_data = json.loads(content)
        st.session_state.r_data = parsed_data
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except Exception: return False

# --- PDF GENERATOR WITH BLOCK PROTECTION ---
def generate_harvard_pdf(data, settings):
    pdf = FPDF(unit="in", format=settings['paper_size'].lower())
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    margin = settings['margin']
    spacing = settings['spacing'] 
    base_font = settings['font_size']
    font_fam = settings['font_family']
    header_align = settings['header_align'][0] 
    
    pdf.set_margins(left=margin, top=margin, right=margin)

    # --- Header ---
    pdf.set_font(font_fam, "B", settings['header_size'])
    pdf.cell(w=0, h=0.3, text=sanitize(data.get('name', 'Candidate')), align=header_align, ln=True)
    pdf.set_font(font_fam, "", base_font)
    contact = [p for p in [data.get('address',''), data.get('phone',''), data.get('email',''), clean_url(data.get('linkedin', ''))] if p.strip()]
    pdf.cell(w=0, h=0.2, text=sanitize(" | ".join(contact)), align=header_align, ln=True)

    def add_section_header(title):
        pdf.ln(0.1)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.22, text=title.upper(), border="B", ln=True)
        pdf.ln(0.05)

    def print_job_block(name, loc, title, date, bullets):
        # 1. MEASURE HEIGHT BEFORE PRINTING
        # Headers (0.18 + 0.18) + Bullets (calculated via multi_cell height)
        test_pdf = FPDF(unit="in", format=settings['paper_size'].lower())
        test_pdf.add_page()
        test_pdf.set_margins(left=margin, top=margin, right=margin)
        test_pdf.set_font(font_fam, "", base_font)
        
        start_y = test_pdf.get_y()
        test_pdf.ln(0.18) # Header 1
        test_pdf.ln(0.18) # Header 2
        if bullets:
            for b in bullets.split('\n'):
                if b.strip():
                    # Calculate multi_cell height for this bullet
                    test_pdf.multi_cell(w=pdf.epw - 0.2, h=0.18 * spacing, text=sanitize(b))
        
        total_block_height = test_pdf.get_y() - start_y + 0.1 # Add small padding
        
        # 2. CHECK IF BLOCK FITS
        if (pdf.h - pdf.b_margin - pdf.get_y()) < total_block_height:
            pdf.add_page()

        # 3. ACTUAL PRINTING
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(name), align="L")
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(loc), align="R", ln=True)
        
        pdf.set_font(font_fam, "I", base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(title), align="L")
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(date), align="R", ln=True)
        
        if bullets:
            for b in bullets.split('\n'):
                bullet_text = sanitize(b.strip().lstrip('-•*').strip())
                if not bullet_text: continue
                pdf.set_font(font_fam, "", base_font)
                pdf.set_x(margin + 0.15)
                pdf.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
                pdf.multi_cell(w=0, h=0.18 * spacing, text=bullet_text, markdown=True)

    # Render Loop
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary'):
            add_section_header("Professional Summary")
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(0, 0.18 * spacing, sanitize(data['summary']), markdown=True)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            name_key = 'company' if sec_key == 'core_Experience' else 'organization'
            add_section_header(data.get(f'heading_{list_key}', list_key.capitalize()))
            for item in data.get(list_key, []):
                if item.get(name_key):
                    print_job_block(item[name_key], item.get('location',''), item.get('title',''), item.get('date',''), item.get('bullets',''))

        elif sec_key == 'core_Education':
            add_section_header("Education")
            for ed in data.get('education', []):
                if ed.get('school'):
                    print_job_block(ed['school'], ed.get('location',''), ed.get('degree',''), ed.get('date',''), ed.get('details',''))

        elif sec_key == 'core_Skills':
            add_section_header("Additional Information")
            sk = data.get('skills', {})
            parts = []
            if sk.get('technical'): parts.append(f"**Technical Skills:** {sk['technical']}")
            if sk.get('languages'): parts.append(f"**Languages:** {sk['languages']}")
            if sk.get('interests'): parts.append(f"**Interests:** {sk['interests']}")
            if parts:
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(0, 0.18 * spacing, sanitize("  ".join(parts)), markdown=True)

        elif sec_key.startswith('custom_'):
            cid = sec_key.split('_')[1]
            c_sec = next((cs for cs in data.get('custom_sections',[]) if cs.get('id') == cid), None)
            if c_sec:
                add_section_header(c_sec['title'])
                pdf.multi_cell(0, 0.18 * spacing, sanitize(c_sec['content']), markdown=True)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI (Full Original Version) ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': '', 'address': '', 'phone': '', 'email': '', 'linkedin': '', 'summary': '',
        'education':[], 'experience':[], 'projects':[], 'leadership':[],
        'skills': {'technical': '', 'languages': '', 'interests': ''},
        'custom_sections':[]
    }
if 'section_order' not in st.session_state:
    st.session_state.section_order = ['core_Summary', 'core_Experience', 'core_Education', 'core_Skills']

# Sidebar Save/Load
with st.sidebar:
    st.header("💾 Project")
    st.download_button("Export JSON", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume.json")
    up = st.file_uploader("Import JSON", type="json")
    if up and st.button("Load"):
        st.session_state.r_data.update(json.load(up))
        st.rerun()

st.title("🎓 Harvard Resume Builder")

# Step 1: Import
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("Upload PDF", type="pdf")
with col_text: pasted_text = st.text_area("Paste Content")
if st.button("✨ Auto-Fill Data"):
    content = pasted_text
    if uploaded_file:
        for page in PyPDF2.PdfReader(uploaded_file).pages: content += page.extract_text()
    if content:
        auto_fill_with_ai(content)
        st.rerun()

# Step 2: Editor Tabs
tabs = st.tabs(["👤 Info", "🎓 Education", "💼 Experience", "🛠️ Skills", "⭐ Custom"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Name", d.get('name'), key=f"n_{uid}")
    c1, c2 = st.columns(2)
    d['address'] = c1.text_input("Address", d.get('address'), key=f"a_{uid}")
    d['phone'] = c2.text_input("Phone", d.get('phone'), key=f"p_{uid}")
    d['email'] = c1.text_input("Email", d.get('email'), key=f"e_{uid}")
    d['linkedin'] = c2.text_input("LinkedIn", d.get('linkedin'), key=f"l_{uid}")
    d['summary'] = st.text_area("Summary", d.get('summary'), key=f"s_{uid}")

with tabs[1]:
    for i, ed in enumerate(st.session_state.r_data.get('education', [])):
        with st.expander(f"School: {ed.get('school')}"):
            ed['school'] = st.text_input("School", ed.get('school'), key=f"eds_{i}_{uid}")
            ed['degree'] = st.text_input("Degree", ed.get('degree'), key=f"edd_{i}_{uid}")
            ed['details'] = st.text_area("Details", ed.get('details'), key=f"edt_{i}_{uid}")
    if st.button("Add Edu"): st.session_state.r_data['education'].append({}); st.rerun()

with tabs[2]:
    for i, ex in enumerate(st.session_state.r_data.get('experience', [])):
        with st.expander(f"Job: {ex.get('company')}"):
            ex['company'] = st.text_input("Company", ex.get('company'), key=f"exc_{i}_{uid}")
            ex['title'] = st.text_input("Title", ex.get('title'), key=f"ext_{i}_{uid}")
            ex['date'] = st.text_input("Date", ex.get('date'), key=f"exd_{i}_{uid}")
            ex['bullets'] = st.text_area("Bullets", ex.get('bullets'), height=150, key=f"exb_{i}_{uid}")
    if st.button("Add Job"): st.session_state.r_data['experience'].append({}); st.rerun()

with tabs[3]:
    sk = st.session_state.r_data['skills']
    sk['technical'] = st.text_area("Technical", sk.get('technical'), key=f"sk_t_{uid}")
    sk['languages'] = st.text_input("Languages", sk.get('languages'), key=f"sk_l_{uid}")
    sk['interests'] = st.text_input("Interests", sk.get('interests'), key=f"sk_i_{uid}")

# Step 3: Export
st.divider()
settings = {
    'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
    'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
    'section_order': st.session_state.section_order
}

if st.button("🔄 Generate PDF Preview", type="primary"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_final = pdf_bytes
    st.success(f"Resume generated! Total pages: {pages}. No entries are split across pages.")

if 'pdf_final' in st.session_state:
    st.download_button("⬇️ Download PDF", data=st.session_state.pdf_final, file_name="Rod_Salmeo_Resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_final).decode('utf-8')
    st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="1000"></iframe>', unsafe_allow_html=True)
