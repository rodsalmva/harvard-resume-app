import streamlit as st
import streamlit.components.v1 as components
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
    prompt = f"Convert this text into a resume JSON. KEEP ALL DETAILS. Output ONLY JSON.\n\n{text}"
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

def polish_bullet_with_ai(text):
    prompt = f"Rewrite this bullet point to be punchier and metric-driven (STAR method). Original: {text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        return completion.choices[0].message.content.strip()
    except: return text

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
        # Prevents section headers from dangling at the very bottom
        if pdf.get_y() > (pdf.h - margin - 0.75):
            pdf.add_page()
        pdf.ln(0.1)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.22, text=title.upper(), border="B", ln=True)
        pdf.ln(0.05)

    def print_job_block(name, loc, title, date, bullets_text, force_break=False):
        if force_break:
            pdf.add_page()
        
        bullet_list = [b.strip() for b in bullets_text.split('\n') if b.strip()]
        first_bullet = bullet_list[0] if bullet_list else ""
        other_bullets = bullet_list[1:] if len(bullet_list) > 1 else []

        # BLOCK 1: Header + First Bullet (Kept Together)
        with pdf.unbreakable() as doc:
            doc.set_font(font_fam, "B", base_font)
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(name), align="L")
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(loc), align="R", ln=True)
            
            doc.set_font(font_fam, "I", base_font)
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(title), align="L")
            doc.cell(w=doc.epw/2, h=0.18, text=sanitize(date), align="R", ln=True)
            
            if first_bullet:
                bullet_clean = sanitize(first_bullet.lstrip('-•*').strip())
                doc.set_font(font_fam, "", base_font)
                doc.set_x(margin + 0.1)
                doc.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
                doc.multi_cell(w=0, h=0.18 * spacing, text=bullet_clean, markdown=True)

        # BLOCK 2: Remaining Bullets (Allowed to flow to next page)
        for b in other_bullets:
            bullet_clean = sanitize(b.lstrip('-•*').strip())
            pdf.set_font(font_fam, "", base_font)
            pdf.set_x(margin + 0.1)
            pdf.cell(w=0.15, h=0.18 * spacing, text=chr(149)) 
            pdf.multi_cell(w=0, h=0.18 * spacing, text=bullet_clean, markdown=True)

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
                        print_job_block(item[name_key], item.get('location',''), item.get('title',''), item.get('date',''), item.get('bullets',''), item.get('force_break', False))

        elif sec_key == 'core_Education':
            add_section_header("Education")
            for ed in data.get('education', []):
                if ed.get('school'):
                    print_job_block(ed['school'], ed.get('location',''), ed.get('degree',''), ed.get('date',''), ed.get('details',''), ed.get('force_break', False))

        elif sec_key == 'core_Skills':
            add_section_header("Skills & Additional Information")
            sk = data.get('skills', {})
            parts = []
            if sk.get('technical'): parts.append(f"**Technical Skills:** {sk['technical']}")
            if sk.get('languages'): parts.append(f"**Languages:** {sk['languages']}")
            if sk.get('interests'): parts.append(f"**Interests:** {sk['interests']}")
            if parts:
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(0, 0.18 * spacing, sanitize("  ".join(parts)), markdown=True)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())
if 'r_data' not in st.session_state:
    st.session_state.r_data = {'name': '', 'experience':[], 'education':[], 'skills':{}, 'custom_sections':[]}

with st.sidebar:
    st.header("💾 Project")
    st.download_button("Export Data", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume.json")
    up = st.file_uploader("Import Data", type="json")
    if up: 
        st.session_state.r_data.update(json.load(up))

st.title("🎓 Harvard Resume Builder")

# --- IMPORT ---
uploaded_file = st.file_uploader("Upload existing PDF or paste text to auto-fill", type="pdf")
pasted_text = st.text_area("Paste text here")
if st.button("✨ Auto-Fill"):
    content = pasted_text
    if uploaded_file:
        for page in PyPDF2.PdfReader(uploaded_file).pages: content += page.extract_text()
    auto_fill_with_ai(content)
    st.rerun()

# --- EDITING ---
tabs = st.tabs(["👤 Info", "💼 Experience", "🎓 Education", "🛠️ Skills"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Full Name", d.get('name'), key=f"n_{uid}")
    d['email'] = st.text_input("Email", d.get('email'), key=f"e_{uid}")
    d['phone'] = st.text_input("Phone", d.get('phone'), key=f"p_{uid}")
    d['address'] = st.text_input("Location", d.get('address'), key=f"a_{uid}")
    d['linkedin'] = st.text_input("LinkedIn", d.get('linkedin'), key=f"l_{uid}")
    d['summary'] = st.text_area("Summary", d.get('summary'), key=f"s_{uid}")

with tabs[1]:
    st.markdown("### Work Experience")
    for i, ex in enumerate(st.session_state.r_data.get('experience', [])):
        with st.expander(f"Job: {ex.get('company', 'New Entry')}", expanded=True):
            ex['force_break'] = st.checkbox("🚀 Force to New Page", value=ex.get('force_break', False), key=f"brk_{i}")
            ex['company'] = st.text_input("Company", ex.get('company'), key=f"c_{i}_{uid}")
            ex['title'] = st.text_input("Title", ex.get('title'), key=f"t_{i}_{uid}")
            ex['date'] = st.text_input("Dates", ex.get('date'), key=f"d_{i}_{uid}")
            ex['location'] = st.text_input("Location", ex.get('location'), key=f"loc_{i}_{uid}")
            ex['bullets'] = st.text_area("Bullets (One per line)", ex.get('bullets'), key=f"b_{i}_{uid}", help="Tip: You can add extra 'Enters' at the top to move it down.")
            if st.button(f"Polish Bullets {i}"):
                ex['bullets'] = polish_bullet_with_ai(ex['bullets'])
                st.rerun()
    if st.button("Add Job"): st.session_state.r_data['experience'].append({'force_break': False}); st.rerun()

with tabs[2]:
    for i, ed in enumerate(st.session_state.r_data.get('education', [])):
        with st.expander(f"School: {ed.get('school', 'New Entry')}"):
            ed['force_break'] = st.checkbox("🚀 Force to New Page", value=ed.get('force_break', False), key=f"edbrk_{i}")
            ed['school'] = st.text_input("School", ed.get('school'), key=f"eds_{i}")
            ed['degree'] = st.text_input("Degree", ed.get('degree'), key=f"edd_{i}")
            ed['date'] = st.text_input("Dates", ed.get('date'), key=f"edda_{i}")
            ed['details'] = st.text_area("Details", ed.get('details'), key=f"eddet_{i}")
    if st.button("Add Education"): st.session_state.r_data['education'].append({'force_break': False}); st.rerun()

with tabs[3]:
    sk = st.session_state.r_data.get('skills', {})
    st.session_state.r_data['skills']['technical'] = st.text_area("Technical", sk.get('technical'))
    st.session_state.r_data['skills']['languages'] = st.text_input("Languages", sk.get('languages'))
    st.session_state.r_data['skills']['interests'] = st.text_input("Interests", sk.get('interests'))

# --- PREVIEW ---
st.divider()
settings = {
    'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
    'margin': 0.6, 'font_size': 11, 'header_size': 16, 'spacing': 1.15,
    'section_order': ['core_Summary', 'core_Experience', 'core_Education', 'core_Skills']
}

if st.button("🔄 Generate Preview", type="primary"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_final = pdf_bytes

if 'pdf_final' in st.session_state:
    st.download_button("⬇️ Download PDF", data=st.session_state.pdf_final, file_name="Resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_final).decode()
    st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="800"></iframe>', unsafe_allow_html=True)
