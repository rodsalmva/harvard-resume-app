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
    prompt = f"Convert this text into a resume JSON. KEEP ALL DETAILS, DO NOT SHORTEN. Output ONLY JSON.\n\n{text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON API."}, {"role": "user", "content": prompt}],
            temperature=0, 
            response_format={"type": "json_object"}
        )
        parsed_data = json.loads(completion.choices[0].message.content.strip())
        preserved_photo = st.session_state.r_data.get('photo_bytes')
        st.session_state.r_data.update(parsed_data)
        st.session_state.r_data['photo_bytes'] = preserved_photo
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except: return False

def polish_bullet_with_ai(text):
    prompt = f"Rewrite this bullet point to be punchier and metric-driven (STAR method), keeping it to a concise 1-2 lines. Original: {text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        return completion.choices[0].message.content.strip()
    except: return text

# --- PDF GENERATOR (STAY-TOGETHER LOGIC) ---
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

    def print_job_block(name, loc, title, date, bullets_text):
        """Uses the fpdf2 unbreakable context to prevent splitting job entries."""
        with pdf.unbreakable() as doc:
            # 1. Header (Company & Location)
            doc.set_font(font_fam, "B", base_font)
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(name), align="L")
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(loc), align="R", ln=True)
            
            # 2. Subheader (Title & Date)
            doc.set_font(font_fam, "I", base_font)
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(title), align="L")
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(date), align="R", ln=True)
            
            # 3. Bullets
            if bullets_text:
                bullet_list = [b.strip() for b in bullets_text.split('\n') if b.strip()]
                for b in bullet_list:
                    bullet_clean = sanitize(b.lstrip('-•*').strip())
                    doc.set_font(font_fam, "", base_font)
                    doc.set_x(margin + 0.1)
                    doc.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
                    doc.multi_cell(w=0, h=0.18 * spacing, text=bullet_clean, markdown=True)

    # Render Sections
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary'):
            add_section_header("Professional Summary")
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(0, 0.18 * spacing, sanitize(data['summary']), markdown=True)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            name_key = 'company' if sec_key == 'core_Experience' else 'organization'
            if any(item.get(name_key) for item in data.get(list_key, [])):
                add_section_header(data.get(f'heading_{list_key}', list_key.capitalize()))
                for item in data[list_key]:
                    if item.get(name_key):
                        print_job_block(item[name_key], item.get('location',''), item.get('title',''), item.get('date',''), item.get('bullets',''))

        elif sec_key == 'core_Education':
            add_section_header("Education")
            for ed in data.get('education', []):
                if ed.get('school'):
                    print_job_block(ed['school'], ed.get('location',''), ed.get('degree',''), ed.get('date',''), ed.get('details',''))

        elif sec_key == 'core_Skills':
            add_section_header("Skills & Additional Information")
            sk = data.get('skills', {})
            # --- CONSOLIDATED HARVARD STYLE SKILLS ---
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
            if c_sec and c_sec['title'].strip():
                with pdf.unbreakable() as doc:
                    add_section_header(c_sec['title'])
                    doc.set_font(font_fam, "", base_font)
                    doc.multi_cell(0, 0.18 * spacing, sanitize(c_sec['content']), markdown=True)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': '', 'address': '', 'phone': '', 'email': '', 'linkedin': '', 'summary': '',
        'education':[], 'experience':[], 'projects':[], 'leadership':[],
        'skills': {'technical': '', 'languages': '', 'interests': ''},
        'custom_sections':[]
    }

# --- SIDEBAR: SAVE/LOAD ---
with st.sidebar:
    st.header("💾 Project")
    st.download_button("Export JSON Data", data=json.dumps(strip_internal_ids(st.session_state.r_data), indent=2), file_name="my_resume.json")
    up = st.file_uploader("Import JSON Data", type="json")
    if up and st.button("Load Now"):
        st.session_state.r_data.update(json.load(up))
        st.rerun()

st.title("🎓 Elite Harvard Resume Builder")
st.success("Now with 'Unbreakable' block logic — Jobs will never split across pages.")

# --- STEP 1: IMPORT ---
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("Upload PDF", type="pdf")
with col_text: pasted_text = st.text_area("Paste Content")
if st.button("✨ Auto-Fill Resume Data", type="primary"):
    content = pasted_text
    if uploaded_file:
        for page in PyPDF2.PdfReader(uploaded_file).pages: content += page.extract_text()
    if content:
        with st.spinner("Analyzing data..."):
            auto_fill_with_ai(content)
            st.rerun()

# --- STEP 2: EDITING ---
tabs = st.tabs(["👤 Info", "🎓 Education", "💼 Experience", "🛠️ Skills", "⭐ Custom"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Name", d.get('name'), key=f"n_{uid}")
    c1, c2 = st.columns(2)
    d['address'] = c1.text_input("Location", d.get('address'), key=f"a_{uid}")
    d['phone'] = c2.text_input("Phone", d.get('phone'), key=f"p_{uid}")
    d['email'] = c1.text_input("Email", d.get('email'), key=f"e_{uid}")
    d['linkedin'] = c2.text_input("LinkedIn", d.get('linkedin'), key=f"l_{uid}")
    d['summary'] = st.text_area("Summary", d.get('summary'), key=f"s_{uid}", height=100)

with tabs[2]:
    st.info("Experience Blocks are 'unbreakable'—if one is too long for the page, it moves to the next.")
    for i, ex in enumerate(st.session_state.r_data.get('experience', [])):
        with st.expander(f"Job: {ex.get('company', 'New')}", expanded=True):
            ex['company'] = st.text_input("Company", ex.get('company'), key=f"exc_{i}_{uid}")
            ex['title'] = st.text_input("Title", ex.get('title'), key=f"ext_{i}_{uid}")
            ex['date'] = st.text_input("Date", ex.get('date'), key=f"exd_{i}_{uid}")
            ex['location'] = st.text_input("Location", ex.get('location'), key=f"exl_{i}_{uid}")
            ex['bullets'] = st.text_area("Bullets", ex.get('bullets'), key=f"exb_{i}_{uid}", height=150)
            if st.button(f"Polish Bullets {i}", key=f"pbtn_{i}"):
                ex['bullets'] = polish_bullet_with_ai(ex['bullets'])
                st.rerun()
    if st.button("Add Work Experience"): st.session_state.r_data['experience'].append({}); st.rerun()

with tabs[3]:
    sk = st.session_state.r_data['skills']
    sk['technical'] = st.text_area("Technical", sk.get('technical'), key=f"skt_{uid}")
    sk['languages'] = st.text_input("Languages", sk.get('languages'), key=f"skl_{uid}")
    sk['interests'] = st.text_input("Interests", sk.get('interests'), key=f"ski_{uid}")

with tabs[4]:
    for i, cs in enumerate(st.session_state.r_data.get('custom_sections', [])):
        if 'id' not in cs: cs['id'] = str(uuid.uuid4())
        with st.expander(f"Custom Block: {cs.get('title','Unnamed')}", expanded=True):
            cs['title'] = st.text_input("Section Title", cs.get('title'), key=f"cst_{i}")
            cs['content'] = st.text_area("Content", cs.get('content'), key=f"csc_{i}")
    if st.button("Add Custom Block"): st.session_state.r_data['custom_sections'].append({}); st.rerun()

# --- STEP 3: EXPORT ---
st.divider()
settings = {
    'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
    'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
    'section_order': ['core_Summary', 'core_Experience', 'core_Education', 'core_Skills'] + [f"custom_{c.get('id')}" for c in st.session_state.r_data.get('custom_sections',[])]
}

if st.button("🔄 Preview Resume PDF", type="primary"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_final = pdf_bytes
    st.success(f"Generated {pages} page(s). All job blocks kept together.")

if 'pdf_final' in st.session_state:
    st.download_button("⬇️ Download Final PDF", data=st.session_state.pdf_final, file_name="Harvard_Resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_final).decode('utf-8')
    st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="1000"></iframe>', unsafe_allow_html=True)
