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
        return[strip_internal_ids(v) for v in data]
    return data

# --- AI AUTOFOCUS & POLISH LOGIC ---
def auto_fill_with_ai(text, merge=False):
    if merge:
        baseline_data = strip_internal_ids(st.session_state.r_data)
    else:
        baseline_data = {
            "name": "", "address": "", "phone": "", "email": "", "linkedin": "",
            "summary": "", "education": [], "experience":[], "projects":[], "leadership":[],
            "skills": {"technical": "", "languages": "", "interests": ""},
            "custom_sections":[]
        }
        
    prompt = f"""
    You are an advanced AI resume compiler.
    BASELINE JSON: {json.dumps(baseline_data)}
    NEW RAW INPUT: {text}
    
    INSTRUCTIONS:
    1. Incorporate updates into the JSON structure.
    2. Convert all experience into concise STAR-method bullets.
    3. Ensure technical resources or home office setups are placed in 'custom_sections'.
    4. Output ONLY the raw JSON.
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON API."}, {"role": "user", "content": prompt}],
            temperature=0, 
            response_format={"type": "json_object"}
        )
        content = completion.choices[0].message.content.strip()
        parsed_data = json.loads(content)
        
        preserved_photo = st.session_state.r_data.get('photo_bytes')
        st.session_state.r_data = parsed_data
        st.session_state.r_data['photo_bytes'] = preserved_photo
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except Exception as e:
        st.error(f"AI Error: {e}")
        return False

def polish_bullet_with_ai(text):
    prompt = f"""Rewrite these bullets for a 1-page Harvard Resume. 
    Make them extremely concise (max 1 line each) using the STAR method. 
    Bold key metrics using **bold**.
    Original: {text}"""
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        return completion.choices[0].message.content.strip()
    except Exception: return text

# --- PDF GENERATOR (fpdf2) ---
def generate_harvard_pdf(data, settings):
    paper_w = 8.5 if settings['paper_size'] == "Letter" else 8.27
    paper_h = 11.0 if settings['paper_size'] == "Letter" else 11.69
    
    pdf = FPDF(unit="in", format=settings['paper_size'].lower())
    author_name = sanitize(data.get('name', 'Candidate'))
    pdf.set_title(f"{author_name} - Resume")
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    margin = settings['margin']
    spacing = settings['spacing'] 
    base_font = settings['font_size']
    font_fam = settings['font_family']
    header_align = settings['header_align'][0] 
    accent_rgb = settings['accent_rgb']
    
    pdf.set_margins(left=margin, top=margin, right=margin)

    # Header
    pdf.set_text_color(*accent_rgb)
    pdf.set_font(font_fam, "B", settings['header_size'])
    pdf.cell(w=0, h=0.3, text=author_name, align=header_align, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font_fam, "", base_font)
    contact_parts = [p for p in[data.get('address',''), data.get('phone',''), data.get('email',''), clean_url(data.get('linkedin', ''))] if p.strip()]
    pdf.cell(w=0, h=0.2, text=sanitize("  |  ".join(contact_parts)), align=header_align, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(0.05)
    
    def check_page_break(required_height):
        if pdf.get_y() > (paper_h - margin - required_height): pdf.add_page()
            
    def add_section_header(title):
        check_page_break(0.4)
        pdf.set_text_color(*accent_rgb)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.22, text=title.upper(), border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(0.05)
        
    def add_left_right(left_text, right_text, left_style, right_style):
        y_before = pdf.get_y()
        pdf.set_font(font_fam, left_style, base_font)
        pdf.cell(w=0, h=0.18, text=sanitize(left_text), align="L")
        if right_text:
            pdf.set_y(y_before)
            pdf.set_font(font_fam, right_style, base_font)
            pdf.cell(w=0, h=0.18, text=sanitize(right_text), align="R", new_x="LMARGIN", new_y="NEXT")
        else: pdf.ln(0.18)

    def print_bullets(bullets_text):
        if not bullets_text.strip(): return
        bullets = [b.strip() for b in bullets_text.split('\n') if b.strip()]
        for bullet in bullets:
            bullet = sanitize(bullet.lstrip('-').lstrip('•').lstrip('*').strip())
            if not bullet: continue
            pdf.set_font(font_fam, "", base_font)
            pdf.cell(w=0.15, h=0.18 * spacing, text=chr(149), align="R") 
            pdf.set_x(margin + 0.2)
            pdf.multi_cell(w=0, h=0.18 * spacing, text=bullet, markdown=True, new_x="LMARGIN", new_y="NEXT")

    # Render Sections
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary', '').strip():
            add_section_header(data.get('heading_summary', 'Professional Summary'))
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(w=0, h=0.18 * spacing, text=sanitize(data['summary']), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.05)

        elif sec_key == 'core_Education' and any(ed.get('school') for ed in data.get('education',[])):
            add_section_header(data.get('heading_education', 'Education'))
            for ed in data['education']:
                if not ed.get('school'): continue
                add_left_right(ed['school'], ed.get('location', ''), "B", "B")
                add_left_right(ed.get('degree', ''), ed.get('date', ''), "I", "I")
                if ed.get('details'):
                    pdf.set_font(font_fam, "", base_font)
                    pdf.multi_cell(w=0, h=0.18, text=sanitize(ed['details']), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.05)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            name_key = 'company' if sec_key == 'core_Experience' else 'organization'
            if any(item.get(name_key) for item in data.get(list_key,[])):
                add_section_header(data.get(f'heading_{list_key}', list_key.capitalize()))
                for item in data[list_key]:
                    if not item.get(name_key): continue
                    add_left_right(item[name_key], item.get('location', ''), "B", "B")
                    add_left_right(item.get('title', ''), item.get('date', ''), "I", "I")
                    print_bullets(item.get('bullets', ''))
            pdf.ln(0.05)

        elif sec_key == 'core_Projects' and any(p.get('title') for p in data.get('projects',[])):
            add_section_header(data.get('heading_projects', 'Projects'))
            for p in data['projects']:
                if not p.get('title'): continue
                add_left_right(p['title'], p.get('date', ''), "B", "B")
                print_bullets(p.get('bullets', ''))
            pdf.ln(0.05)

        elif sec_key == 'core_Skills' and any(data.get('skills', {}).values()):
            # --- ADVICE A: CONSOLIDATED PARAGRAPH ---
            add_section_header(data.get('heading_skills', 'Additional Information'))
            sk = data.get('skills', {})
            parts = []
            if sk.get('technical'): parts.append(f"**Technical Skills:** {sk['technical']}")
            if sk.get('languages'): parts.append(f"**Languages:** {sk['languages']}")
            if sk.get('interests'): parts.append(f"**Interests:** {sk['interests']}")
            if parts:
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(w=0, h=0.18 * spacing, text=sanitize("  ".join(parts)), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.05)

        elif sec_key.startswith('custom_'):
            cid = sec_key.split('_')[1]
            c_sec = next((cs for cs in data.get('custom_sections',[]) if cs.get('id') == cid), None)
            if c_sec and c_sec['title'].strip():
                add_section_header(c_sec['title'])
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(w=0, h=0.18 * spacing, text=sanitize(c_sec['content']), markdown=True, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(0.05)

    return bytes(pdf.output()), pdf.page_no()

# --- STREAMLIT UI SETUP ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': '', 'address': '', 'phone': '', 'email': '', 'linkedin': '', 'summary': '',
        'heading_summary': 'Professional Summary', 'heading_education': 'Education', 
        'heading_experience': 'Experience', 'heading_projects': 'Projects', 
        'heading_leadership': 'Leadership', 'heading_skills': 'Additional Information',
        'education':[{'school': '', 'location': '', 'degree': '', 'date': '', 'details': ''}],
        'experience':[{'company': '', 'location': '', 'title': '', 'date': '', 'bullets': ''}],
        'projects': [], 'leadership':[],
        'skills': {'technical': '', 'languages': '', 'interests': ''},
        'custom_sections':[], 'photo_bytes': None
    }
if 'section_order' not in st.session_state:
    st.session_state.section_order =['core_Summary', 'core_Education', 'core_Experience', 'core_Projects', 'core_Leadership', 'core_Skills']

# --- SIDEBAR & MAIN UI ---
with st.sidebar:
    st.header("💾 Project Management")
    st.download_button("⬇️ Download Data (.json)", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume.json")
    uploaded_json = st.file_uploader("⬆️ Load Data (.json)", type="json")
    if uploaded_json and st.button("Load Now"):
        st.session_state.r_data.update(json.load(uploaded_json))
        st.rerun()

st.title("🎓 Harvard Style Elite Resume Builder")
st.info("Goal: 1-Page High-Density Professionalism.")

# --- STEP 1: IMPORT ---
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("Upload Current Resume", type="pdf")
with col_text: pasted_text = st.text_area("Paste text or instructions here", height=100)

if st.button("✨ Auto-Format with AI", type="primary"):
    combined = pasted_text
    if uploaded_file:
        reader = PyPDF2.PdfReader(uploaded_file)
        for page in reader.pages: combined += page.extract_text()
    if combined: 
        auto_fill_with_ai(combined)
        st.rerun()

# --- STEP 2: EDITING ---
tabs = st.tabs(["👤 Info", "🎓 Edu", "💼 Exp", "🚀 Proj", "🛠️ Skills", "⭐ Custom"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Name", d['name'], key=f"n_{uid}")
    c1, c2 = st.columns(2)
    d['address'] = c1.text_input("Location", d['address'], key=f"a_{uid}")
    d['phone'] = c2.text_input("Phone", d['phone'], key=f"p_{uid}")
    d['email'] = c1.text_input("Email", d['email'], key=f"e_{uid}")
    d['linkedin'] = c2.text_input("LinkedIn", d['linkedin'], key=f"l_{uid}")
    d['summary'] = st.text_area("Summary", d['summary'], key=f"s_{uid}")

with tabs[2]:
    for i, exp in enumerate(st.session_state.r_data['experience']):
        with st.expander(f"{exp.get('company', 'Job')} - {exp.get('title', '')}", expanded=True):
            exp['company'] = st.text_input("Company", exp.get('company'), key=f"c_{i}_{uid}")
            exp['title'] = st.text_input("Title", exp.get('title'), key=f"t_{i}_{uid}")
            exp['bullets'] = st.text_area("Bullets", exp.get('bullets'), key=f"b_{i}_{uid}")
            if st.button(f"✨ Polish for 1-Page (AI)", key=f"pol_{i}"):
                exp['bullets'] = polish_bullet_with_ai(exp['bullets'])
                st.rerun()

with tabs[4]:
    st.info("Harvard Style: Combined paragraph at the bottom.")
    sk = st.session_state.r_data['skills']
    sk['technical'] = st.text_area("Technical Skills", sk.get('technical'), key=f"skt_{uid}")
    sk['languages'] = st.text_input("Languages", sk.get('languages'), key=f"skl_{uid}")
    sk['interests'] = st.text_input("Interests", sk.get('interests'), key=f"ski_{uid}")

# --- STEP 3: EXPORT ---
st.divider()
st.markdown("### 👁️ Live Alignment & Export")
strict = st.toggle("Strict Mode (One Page Forced)", value=True)

if strict:
    settings = {
        'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
        'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
        'photo_position': 'Hide Photo', 'photo_size': 0, 'accent_rgb': (0,0,0),
        'section_order': st.session_state.section_order
    }
else:
    settings = {
        'paper_size': 'Letter', 'font_family': 'Arial', 'header_align': 'Left',
        'margin': 0.5, 'font_size': 10, 'header_size': 14, 'spacing': 0.9,
        'photo_position': 'Hide Photo', 'photo_size': 0, 'accent_rgb': (0,0,0),
        'section_order': st.session_state.section_order
    }

if st.button("🔄 Refresh Preview", type="primary"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_bytes = pdf_bytes
    if pages > 1: st.error("🚨 Warning: Currently 2 pages. Use 'Polish AI' to shorten bullets.")

if 'pdf_bytes' in st.session_state:
    st.download_button("⬇️ Download Final Resume", data=st.session_state.pdf_bytes, file_name="Rod_Salmeo_Resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_bytes).decode('utf-8')
    pdf_html = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="800"></iframe>'
    st.markdown(pdf_html, unsafe_allow_html=True)
