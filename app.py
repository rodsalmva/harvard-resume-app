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

# --- AI LOGIC ---
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
    You are an advanced AI resume compiler and editor.
    BASELINE JSON: {json.dumps(baseline_data)}
    NEW RAW INPUT: {text}
    
    CRITICAL: Output a valid JSON object. Incorporate new data into the structure. 
    Format bullets using the STAR method. 
    Keep technical skills, languages, and interests separate in the "skills" object.
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
        
        # Preserve photo/UI
        preserved_photo = st.session_state.r_data.get('photo_bytes')
        for key in ['heading_summary', 'heading_education', 'heading_experience', 'heading_projects', 'heading_leadership', 'heading_skills']:
            parsed_data[key] = st.session_state.r_data.get(key, key.split('_')[1].capitalize() if '_' in key else "Skills")
            
        st.session_state.r_data = parsed_data
        st.session_state.r_data['photo_bytes'] = preserved_photo
        st.session_state.ui_gen_id = str(uuid.uuid4())
        return True
    except Exception as e:
        st.error(f"AI Error: {e}")
        return False

def polish_bullet_with_ai(text):
    prompt = f"Rewrite these resume bullets to be metric-driven (STAR method). Use **text** for bolding metrics. \n\n{text}"
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        return completion.choices[0].message.content.strip()
    except Exception: return text

# --- PDF GENERATOR ---
def generate_harvard_pdf(data, settings):
    paper_w = 8.5 if settings['paper_size'] == "Letter" else 8.27
    paper_h = 11.0 if settings['paper_size'] == "Letter" else 11.69
    
    pdf = FPDF(unit="in", format=settings['paper_size'].lower())
    author_name = sanitize(data.get('name', 'Candidate'))
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    margin = settings['margin']
    spacing = settings['spacing'] 
    base_font = settings['font_size']
    font_fam = settings['font_family']
    header_align = settings['header_align'][0] 
    accent_rgb = settings['accent_rgb']
    pdf.set_margins(left=margin, top=margin, right=margin)

    # Photo Handling
    if not settings['strict_mode'] and data.get('photo_bytes') and settings['photo_position'] != "Hide Photo":
        try:
            img = Image.open(io.BytesIO(data['photo_bytes']))
            p_w = settings['photo_size']
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img.convert('RGB').save(tmp.name, format="JPEG")
                pdf.image(tmp.name, x=(margin if settings['photo_position'] == "Top Left" else paper_w - margin - p_w), y=margin, w=p_w)
            os.remove(tmp.name)
        except: pass

    # Header
    pdf.set_text_color(*accent_rgb)
    pdf.set_font(font_fam, "B", settings['header_size'])
    pdf.cell(w=0, h=0.3, text=author_name, align=header_align, ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font_fam, "", base_font)
    contact = [p for p in [data.get('address',''), data.get('phone',''), data.get('email',''), clean_url(data.get('linkedin', ''))] if p.strip()]
    pdf.cell(w=0, h=0.2, text=sanitize(" | ".join(contact)), align=header_align, ln=True)
    pdf.ln(0.1)

    def add_section_header(title):
        pdf.set_text_color(*accent_rgb)
        pdf.set_draw_color(*accent_rgb)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.25, text=title.upper(), border="B", ln=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(0.05)
        
    def add_left_right(left, right, l_style="B", r_style=""):
        pdf.set_font(font_fam, l_style, base_font)
        pdf.cell(w=pdf.epw/2, h=0.2, text=sanitize(left), align="L")
        pdf.set_font(font_fam, r_style, base_font)
        pdf.cell(w=pdf.epw/2, h=0.2, text=sanitize(right), align="R", ln=True)

    def print_bullets(bullets_text):
        for b in bullets_text.split('\n'):
            bullet = sanitize(b.strip().lstrip('-•*').strip())
            if not bullet: continue
            pdf.set_font(font_fam, "", base_font)
            pdf.set_x(margin + 0.15)
            pdf.cell(w=0.15, h=0.2 * spacing, text=chr(149)) 
            pdf.multi_cell(w=0, h=0.2 * spacing, text=bullet, markdown=True)

    # Rendering
    for sec_key in settings['section_order']:
        if sec_key == 'core_Summary' and data.get('summary'):
            add_section_header(data.get('heading_summary', 'Summary'))
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(0, 0.2 * spacing, sanitize(data['summary']), markdown=True)
            pdf.ln(0.1)

        elif sec_key == 'core_Education':
            add_section_header(data.get('heading_education', 'Education'))
            for ed in data.get('education', []):
                if not ed.get('school'): continue
                add_left_right(ed['school'], ed.get('location', ''), "B", "B")
                add_left_right(ed.get('degree', ''), ed.get('date', ''), "I", "I")
                if ed.get('details'):
                    pdf.set_font(font_fam, "", base_font)
                    pdf.multi_cell(0, 0.18, sanitize(ed['details']), markdown=True)
            pdf.ln(0.1)

        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            add_section_header(data.get(f'heading_{list_key}', list_key.capitalize()))
            for item in data.get(list_key, []):
                add_left_right(item.get('company' if list_key=='experience' else 'organization', ''), item.get('location', ''), "B", "B")
                add_left_right(item.get('title', ''), item.get('date', ''), "I", "I")
                print_bullets(item.get('bullets', ''))
            pdf.ln(0.1)

        elif sec_key == 'core_Projects':
            add_section_header(data.get('heading_projects', 'Projects'))
            for p in data.get('projects', []):
                add_left_right(p.get('title',''), p.get('date',''), "B", "B")
                if p.get('role'): pdf.cell(0, 0.18, sanitize(p['role']), ln=True, font_style="I")
                print_bullets(p.get('bullets', ''))
            pdf.ln(0.1)

        elif sec_key == 'core_Skills':
            # --- HARVARD STYLE CONSOLIDATED SKILLS (Advice A) ---
            add_section_header(data.get('heading_skills', 'Skills & Interests'))
            
            skills_html = ""
            sk_data = data.get('skills', {})
            parts = []
            if sk_data.get('technical'): parts.append(f"**Technical Skills:** {sk_data['technical']}")
            if sk_data.get('languages'): parts.append(f"**Languages:** {sk_data['languages']}")
            if sk_data.get('interests'): parts.append(f"**Interests:** {sk_data['interests']}")
            
            if parts:
                pdf.set_font(font_fam, "", base_font)
                # Join with spaces or semicolon for Harvard look
                full_skills_text = " ".join(parts)
                pdf.multi_cell(w=0, h=0.2 * spacing, text=sanitize(full_skills_text), markdown=True)
            pdf.ln(0.1)

        elif sec_key.startswith('custom_'):
            cid = sec_key.split('_')[1]
            c_sec = next((cs for cs in data.get('custom_sections',[]) if cs.get('id') == cid), None)
            if c_sec:
                add_section_header(c_sec['title'])
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(0, 0.2 * spacing, sanitize(c_sec['content']), markdown=True)
                pdf.ln(0.1)

    return pdf.output(), pdf.page_no()

# --- STREAMLIT UI ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")
if 'ui_gen_id' not in st.session_state: st.session_state.ui_gen_id = str(uuid.uuid4())
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': '', 'address': '', 'phone': '', 'email': '', 'linkedin': '', 'summary': '',
        'heading_summary': 'Professional Summary', 'heading_education': 'Education', 
        'heading_experience': 'Experience', 'heading_projects': 'Projects', 
        'heading_leadership': 'Leadership & Extracurriculars', 'heading_skills': 'Skills & Interests',
        'education':[{'school': '', 'location': '', 'degree': '', 'date': '', 'details': ''}],
        'experience':[{'company': '', 'location': '', 'title': '', 'date': '', 'bullets': ''}],
        'projects': [], 'leadership':[],
        'skills': {'technical': '', 'languages': '', 'interests': ''},
        'custom_sections':[], 'photo_bytes': None
    }
if 'section_order' not in st.session_state:
    st.session_state.section_order = ['core_Education', 'core_Experience', 'core_Projects', 'core_Leadership', 'core_Skills']

# Sidebar & Load logic...
with st.sidebar:
    st.header("💾 Save / Load")
    st.download_button("⬇️ Export Data", data=json.dumps(strip_internal_ids(st.session_state.r_data)), file_name="resume.json")
    uploaded_json = st.file_uploader("⬆️ Import Data", type="json")
    if uploaded_json and st.button("Load"):
        st.session_state.r_data.update(json.load(uploaded_json))
        st.rerun()

st.title("🎓 Harvard Resume Builder")

# Import Section...
st.markdown("### 📄 Step 1: Import")
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("Upload PDF", type="pdf")
with col_text: pasted_text = st.text_area("Or Paste Text / Instructions", height=100)

if st.button("✨ Process with AI"):
    combined = pasted_text
    if uploaded_file:
        reader = PyPDF2.PdfReader(uploaded_file)
        for page in reader.pages: combined += page.extract_text()
    if combined: 
        auto_fill_with_ai(combined)
        st.rerun()

# Editing Tabs...
st.divider()
tabs = st.tabs(["👤 Info", "🎓 Education", "💼 Experience", "🚀 Projects", "🛠️ Skills", "⭐ Custom"])
uid = st.session_state.ui_gen_id

with tabs[0]:
    d = st.session_state.r_data
    d['name'] = st.text_input("Full Name", d['name'], key=f"n_{uid}")
    c1, c2 = st.columns(2)
    d['address'] = c1.text_input("Location", d['address'], key=f"a_{uid}")
    d['phone'] = c2.text_input("Phone", d['phone'], key=f"p_{uid}")
    d['email'] = c1.text_input("Email", d['email'], key=f"e_{uid}")
    d['linkedin'] = c2.text_input("LinkedIn", d['linkedin'], key=f"l_{uid}")
    d['summary'] = st.text_area("Summary (Optional)", d['summary'], key=f"s_{uid}")

with tabs[1]:
    for i, ed in enumerate(st.session_state.r_data['education']):
        with st.expander(f"School {i+1}", expanded=True):
            ed['school'] = st.text_input("School", ed.get('school'), key=f"ed_s_{i}_{uid}")
            ed['degree'] = st.text_input("Degree", ed.get('degree'), key=f"ed_d_{i}_{uid}")
    if st.button("Add Education"): st.session_state.r_data['education'].append({}); st.rerun()

with tabs[2]:
    for i, ex in enumerate(st.session_state.r_data['experience']):
        with st.expander(f"Job {i+1}", expanded=True):
            ex['company'] = st.text_input("Company", ex.get('company'), key=f"ex_c_{i}_{uid}")
            ex['bullets'] = st.text_area("Bullets", ex.get('bullets'), key=f"ex_b_{i}_{uid}")
            if st.button(f"Polish Bullets {i}", key=f"p_{i}"):
                ex['bullets'] = polish_bullet_with_ai(ex['bullets'])
                st.rerun()
    if st.button("Add Experience"): st.session_state.r_data['experience'].append({}); st.rerun()

with tabs[4]:
    st.info("Harvard Style: These will be combined into one 'Skills & Interests' block.")
    sk = st.session_state.r_data['skills']
    sk['technical'] = st.text_area("Technical Skills", sk.get('technical'), help="Comma separated", key=f"sk_t_{uid}")
    sk['languages'] = st.text_input("Languages", sk.get('languages'), key=f"sk_l_{uid}")
    sk['interests'] = st.text_input("Interests", sk.get('interests'), key=f"sk_i_{uid}")

# Order & Export...
st.divider()
st.markdown("### 👁️ Step 3: Export")
strict = st.toggle("Strict Harvard Mode", value=True)

if strict:
    settings = {
        'strict_mode': True, 'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
        'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
        'photo_position': 'Hide Photo', 'photo_size': 0, 'accent_rgb': (0,0,0),
        'section_order': st.session_state.section_order
    }
else:
    settings = {
        'strict_mode': False, 'paper_size': 'Letter', 'font_family': 'Arial', 'header_align': 'Left',
        'margin': 0.5, 'font_size': 10, 'header_size': 14, 'spacing': 1.0,
        'photo_position': 'Hide Photo', 'photo_size': 0, 'accent_rgb': (0,0,0),
        'section_order': st.session_state.section_order
    }

if st.button("🔄 Generate Preview"):
    pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
    st.session_state.pdf_preview = pdf_bytes
    if pages > 1: st.warning("⚠️ Resume exceeds 1 page!")

if 'pdf_preview' in st.session_state:
    st.download_button("⬇️ Download PDF", data=st.session_state.pdf_preview, file_name="resume.pdf")
    b64 = base64.b64encode(st.session_state.pdf_preview).decode()
    pdf_display = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="800" type="application/pdf"></iframe>'
    st.markdown(pdf_display, unsafe_allow_html=True)
