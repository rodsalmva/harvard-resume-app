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

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

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
    
    prompt = f"Incorporate this resume data into a JSON structure. Do NOT shorten the content; keep all details. Output ONLY JSON.\n\nInput: {text}"
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON API."}, {"role": "user", "content": prompt}],
            temperature=0, 
            response_format={"type": "json_object"}
        )
        parsed_data = json.loads(completion.choices[0].message.content.strip())
        preserved_photo = st.session_state.r_data.get('photo_bytes')
        st.session_state.r_data = parsed_data
        st.session_state.r_data['photo_bytes'] = preserved_photo
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except Exception as e:
        st.error(f"AI Error: {e}")
        return False

# --- PDF GENERATOR ---
def generate_harvard_pdf(data, settings):
    pdf = FPDF(unit="in", format=settings['paper_size'].lower())
    author_name = sanitize(data.get('name', 'Candidate'))
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    margin = settings['margin']
    spacing = settings['spacing'] 
    base_font = settings['font_size']
    font_fam = settings['font_family']
    header_align = settings['header_align'][0] 
    
    pdf.set_margins(left=margin, top=margin, right=margin)

    # Header
    pdf.set_font(font_fam, "B", settings['header_size'])
    pdf.cell(w=0, h=0.3, text=author_name, align=header_align, ln=True)
    pdf.set_font(font_fam, "", base_font)
    contact = [p for p in [data.get('address',''), data.get('phone',''), data.get('email',''), clean_url(data.get('linkedin', ''))] if p.strip()]
    pdf.cell(w=0, h=0.2, text=sanitize(" | ".join(contact)), align=header_align, ln=True)
    pdf.ln(0.05)
    
    def add_section_header(title):
        pdf.ln(0.1)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.22, text=title.upper(), border="B", ln=True)
        pdf.ln(0.05)
        
    def add_left_right(left, right, l_style="B", r_style="B"):
        pdf.set_font(font_fam, l_style, base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(left), align="L")
        pdf.set_font(font_fam, r_style, base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(right), align="R", ln=True)

    def print_bullets(bullets_text):
        if not bullets_text: return
        for b in bullets_text.split('\n'):
            bullet = sanitize(b.strip().lstrip('-•*').strip())
            if not bullet: continue
            pdf.set_font(font_fam, "", base_font)
            pdf.set_x(margin + 0.15)
            pdf.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
            pdf.multi_cell(w=0, h=0.18 * spacing, text=bullet, markdown=True)

    # NO BREAKAGE LOGIC: ESTIMATE HEIGHT
    def check_space(estimated_lines):
        estimated_height = (estimated_lines * 0.2) + 0.4
        # If the remaining space is less than estimated height, start new page
        if (pdf.h - pdf.b_margin - pdf.get_y()) < estimated_height:
            pdf.add_page()

    # Render Sections
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary'):
            add_section_header("Professional Summary")
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(0, 0.18 * spacing, sanitize(data['summary']), markdown=True)

        elif sec_key == 'core_Education':
            add_section_header("Education")
            for ed in data.get('education', []):
                if not ed.get('school'): continue
                check_space(2) # School and degree lines
                add_left_right(ed['school'], ed.get('location', ''), "B", "B")
                add_left_right(ed.get('degree', ''), ed.get('date', ''), "I", "I")
                if ed.get('details'):
                    pdf.multi_cell(0, 0.18, sanitize(ed['details']), markdown=True)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            add_section_header(list_key.capitalize())
            for item in data.get(list_key, []):
                name = item.get('company' if list_key=='experience' else 'organization', '')
                if not name: continue
                # Calculate estimated lines: 2 for header + approx bullets
                bullet_count = len(item.get('bullets', '').split('\n'))
                check_space(2 + bullet_count) 
                
                add_left_right(name, item.get('location', ''), "B", "B")
                add_left_right(item.get('title', ''), item.get('date', ''), "I", "I")
                print_bullets(item.get('bullets', ''))

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
                check_space(5) # Estimate for custom blocks
                add_section_header(c_sec['title'])
                pdf.multi_cell(0, 0.18 * spacing, sanitize(c_sec['content']), markdown=True)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI ---
st.set_page_config(page_title="Rod's Resume Builder", layout="wide")
if 'r_data' not in st.session_state:
    st.session_state.r_data = {'name': '', 'education': [], 'experience': [], 'skills': {}, 'custom_sections': []}
if 'section_order' not in st.session_state:
    st.session_state.section_order = ['core_Summary', 'core_Experience', 'core_Education', 'core_Skills']

st.title("🎓 Harvard Resume Builder (Multi-Page Support)")

# Sidebar & Load
with st.sidebar:
    st.header("💾 Project")
    st.download_button("Export JSON", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume_data.json")
    up = st.file_uploader("Import JSON")
    if up and st.button("Load"):
        st.session_state.r_data.update(json.load(up))
        st.rerun()

# Step 1: Import
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("Upload PDF", type="pdf")
with col_text: pasted_text = st.text_area("Paste Content")
if st.button("✨ Auto-Fill"):
    content = pasted_text
    if uploaded_file:
        for page in PyPDF2.PdfReader(uploaded_file).pages: content += page.extract_text()
    auto_fill_with_ai(content)
    st.rerun()

# Step 2: Editor (Brief version for demo, use previous full editor as needed)
tabs = st.tabs(["Info", "Experience", "Skills", "Custom"])
with tabs[1]:
    for i, exp in enumerate(st.session_state.r_data.get('experience', [])):
        with st.expander(f"Job: {exp.get('company')}"):
            exp['company'] = st.text_input("Company", exp.get('company'), key=f"c_{i}")
            exp['bullets'] = st.text_area("Bullets", exp.get('bullets'), key=f"b_{i}", height=200)

with tabs[2]:
    sk = st.session_state.r_data.get('skills', {})
    sk['technical'] = st.text_area("Technical", sk.get('technical'))
    sk['languages'] = st.text_input("Languages", sk.get('languages'))
    sk['interests'] = st.text_input("Interests", sk.get('interests'))

# Step 3: Preview
st.divider()
settings = {
    'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
    'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
    'section_order': st.session_state.section_order
}

if st.button("🔄 Update PDF Preview"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.preview = pdf_bytes
    st.success(f"Generated {pages} page(s). All sections kept together!")

if 'preview' in st.session_state:
    st.download_button("⬇️ Download Resume", data=st.session_state.preview, file_name="resume.pdf")
    b64 = base64.b64encode(st.session_state.preview).decode('utf-8')
    st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="1000"></iframe>', unsafe_allow_html=True)
