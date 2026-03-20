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

# --- UTILITY: TEXT SANITIZERS ---
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
        return {k: strip_internal_ids(v) for k, v in data.items() if k not in ['_id']}
    elif isinstance(data, list):
        return [strip_internal_ids(v) for v in data]
    return data

# --- AI LOGIC ---
def auto_fill_with_ai(text):
    prompt = f"Convert this text into a resume JSON. Output ONLY JSON.\n\n{text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON API."}, {"role": "user", "content": prompt}],
            temperature=0, 
            response_format={"type": "json_object"}
        )
        parsed_data = json.loads(completion.choices[0].message.content.strip())
        st.session_state.r_data.update(parsed_data)
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except: return False

# --- PDF GENERATOR ---
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
        if pdf.get_y() > (pdf.h - margin - 0.75):
            pdf.add_page()
        pdf.ln(0.1)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.22, text=title.upper(), border="B", ln=True)
        pdf.ln(0.05)

    def print_job_block(name, loc, title, date, bullets_text, force_break=False, top_padding=0):
        # 1. Handle Manual Push Down
        if force_break:
            pdf.add_page()
        elif top_padding > 0:
            pdf.ln(top_padding)

        bullet_list = [b.strip() for b in bullets_text.split('\n') if b.strip()]
        
        # 2. Aggressive "Stay Together" Logic
        # If we are in the bottom 2 inches of the page, move the whole block to next page
        if pdf.get_y() > (pdf.h - margin - 1.5):
            pdf.add_page()

        # Render Header
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(name), align="L")
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(loc), align="R", ln=True)
        
        pdf.set_font(font_fam, "I", base_font)
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(title), align="L")
        pdf.cell(w=pdf.epw/2, h=0.18, text=sanitize(date), align="R", ln=True)
        
        # Render Bullets
        pdf.set_font(font_fam, "", base_font)
        for b in bullet_list:
            bullet_clean = sanitize(b.lstrip('-•*').strip())
            pdf.set_x(margin + 0.1)
            pdf.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
            pdf.multi_cell(w=0, h=0.18 * spacing, text=bullet_clean, markdown=True)
        pdf.ln(0.05)

    # Render Sections
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary'):
            add_section_header("Professional Summary")
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(0, 0.18 * spacing, sanitize(data['summary']), markdown=True)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            if data.get(list_key):
                add_section_header(list_key.capitalize())
                for item in data[list_key]:
                    print_job_block(
                        item.get('company') or item.get('organization', ''), 
                        item.get('location',''), item.get('title',''), item.get('date',''), 
                        item.get('bullets',''), 
                        item.get('force_break', False),
                        item.get('top_padding', 0)
                    )

        elif sec_key == 'core_Education':
            add_section_header("Education")
            for ed in data.get('education', []):
                print_job_block(ed.get('school',''), ed.get('location',''), ed.get('degree',''), ed.get('date',''), ed.get('details',''), ed.get('force_break', False))

        elif sec_key == 'core_Skills':
            add_section_header("Skills & Additional Information")
            sk = data.get('skills', {})
            pdf.set_font(font_fam, "", base_font)
            for label, key in [("Technical", "technical"), ("Languages", "languages"), ("Interests", "interests")]:
                if sk.get(key):
                    pdf.multi_cell(0, 0.18 * spacing, sanitize(f"**{label}:** {sk[key]}"), markdown=True)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI ---
st.set_page_config(page_title="Resume Builder", layout="wide")
if 'r_data' not in st.session_state:
    st.session_state.r_data = {'name': '', 'experience':[], 'education':[], 'skills':{}}
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())

st.title("🎓 Elite Harvard Resume Builder")

# --- STEP 1: IMPORT ---
pasted_text = st.text_area("Paste current resume text to auto-fill:")
if st.button("✨ Auto-Fill Data"):
    with st.spinner("AI is processing..."):
        auto_fill_with_ai(pasted_text)
        st.rerun()

# --- STEP 2: EDITING ---
tabs = st.tabs(["👤 Info", "💼 Experience", "🎓 Education", "🛠️ Skills"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Name", d.get('name'))
    d['email'] = st.text_input("Email", d.get('email'))
    d['phone'] = st.text_input("Phone", d.get('phone'))
    d['address'] = st.text_input("Location", d.get('address'))
    d['linkedin'] = st.text_input("LinkedIn", d.get('linkedin'))
    d['summary'] = st.text_area("Summary", d.get('summary'))

with tabs[1]:
    st.info("If a job breaks across pages, use 'Force to New Page' or add 'Top Padding'.")
    for i, ex in enumerate(st.session_state.r_data.get('experience', [])):
        with st.expander(f"Job: {ex.get('company', 'New Entry')}", expanded=True):
            c1, c2 = st.columns(2)
            ex['force_break'] = c1.checkbox("🚀 Force to New Page", value=ex.get('force_break', False), key=f"f_{i}")
            ex['top_padding'] = c2.slider("📏 Add Top Space (inches)", 0.0, 2.0, float(ex.get('top_padding', 0.0)), key=f"s_{i}")
            
            ex['company'] = st.text_input("Company", ex.get('company'), key=f"co_{i}")
            ex['title'] = st.text_input("Title", ex.get('title'), key=f"ti_{i}")
            ex['date'] = st.text_input("Dates", ex.get('date'), key=f"da_{i}")
            ex['location'] = st.text_input("Location", ex.get('location'), key=f"lo_{i}")
            ex['bullets'] = st.text_area("Bullets", ex.get('bullets'), key=f"bu_{i}", height=150)
    if st.button("Add Job"): st.session_state.r_data['experience'].append({}); st.rerun()

with tabs[2]:
    for i, ed in enumerate(st.session_state.r_data.get('education', [])):
        with st.expander(f"Education: {ed.get('school', 'New Entry')}"):
            ed['school'] = st.text_input("School", ed.get('school'), key=f"eds_{i}")
            ed['degree'] = st.text_input("Degree", ed.get('degree'), key=f"edd_{i}")
            ed['details'] = st.text_area("Details", ed.get('details'), key=f"edt_{i}")
    if st.button("Add Education"): st.session_state.r_data['education'].append({}); st.rerun()

with tabs[3]:
    sk = st.session_state.r_data.get('skills', {})
    st.session_state.r_data['skills']['technical'] = st.text_area("Technical", sk.get('technical'))
    st.session_state.r_data['skills']['languages'] = st.text_input("Languages", sk.get('languages'))
    st.session_state.r_data['skills']['interests'] = st.text_input("Interests", sk.get('interests'))

# --- STEP 3: PREVIEW ---
st.divider()
settings = {
    'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
    'margin': 0.6, 'font_size': 11, 'header_size': 16, 'spacing': 1.15,
    'section_order': ['core_Summary', 'core_Experience', 'core_Education', 'core_Skills']
}

if st.button("🔄 Preview PDF", type="primary"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_final = pdf_bytes

if 'pdf_final' in st.session_state:
    st.download_button("⬇️ Download PDF", data=st.session_state.pdf_final, file_name="Resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_final).decode()
    st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="1000"></iframe>', unsafe_allow_html=True)
