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

# --- AI AUTOFOCUS & POLISH LOGIC ---
def auto_fill_with_ai(text, merge=False):
    if merge:
        # Load the existing data so the AI knows exactly what is already on the resume
        baseline_data = {k: v for k, v in st.session_state.r_data.items() if k != 'photo_bytes'}
    else:
        # Fresh start
        baseline_data = {
            "name": "", "address": "", "phone": "", "email": "", "linkedin": "",
            "summary": "", "education": [], "experience":[], "projects":[], "leadership":[],
            "skills": {"technical": "", "languages": "", "interests": ""},
            "custom_sections":[]
        }
        
    prompt = f"""
    You are an advanced AI resume compiler. 
    Your task is to take the BASELINE JSON (the current state) and the NEW RAW INPUT (uploaded text / newly pasted text), and intelligently process them.
    
    BASELINE JSON:
    {json.dumps(baseline_data)}
    
    NEW RAW INPUT:
    {text}
    
    CRITICAL MERGE RULES:
    1. If the BASELINE JSON is empty, extract all details from the NEW RAW INPUT to populate the JSON.
    2. If the BASELINE JSON already has data, PRESERVE IT! Add new information from the NEW RAW INPUT without overwriting existing valid data.
    3. DO NOT DUPLICATE jobs, schools, or projects. If a job/school in the NEW RAW INPUT matches one in the BASELINE JSON, combine their bullet points and details.
    4. If the NEW RAW INPUT contains explicit small additions (e.g. "Add Python to my skills" or "Add a new bullet to my current job"), follow them precisely by appending to the relevant section.
    
    Strict JSON Structure required (Return ONLY this exact structure, with all keys present):
    {{
      "name": "Full Name", "address": "City, State", "phone": "Phone", "email": "Email", "linkedin": "URL",
      "summary": "Brief professional summary or objective",
      "education":[{{"school": "", "location": "", "degree": "", "date": "", "details": ""}}],
      "experience":[{{"company": "", "location": "", "title": "", "date": "", "bullets": "bullet 1\\nbullet 2"}}],
      "projects":[{{"title": "", "date": "", "role": "", "bullets": ""}}],
      "leadership":[{{"organization": "", "location": "", "title": "", "date": "", "bullets": ""}}],
      "skills": {{"technical": "comma separated", "languages": "comma separated", "interests": "comma separated"}},
      "custom_sections":[{{"title": "", "content": ""}}]
    }}
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a precise JSON compiling API. Return only JSON."}, {"role": "user", "content": prompt}],
            temperature=0, response_format={"type": "json_object"}
        )
        parsed_data = json.loads(completion.choices[0].message.content)
        
        # Preserve existing UI settings & photo
        preserved_photo = st.session_state.r_data.get('photo_bytes')
        for key in['heading_summary', 'heading_education', 'heading_experience', 'heading_projects', 'heading_leadership', 'heading_skills']:
            parsed_data[key] = st.session_state.r_data.get(key, key.split('_')[1].capitalize())
            
        custom_ids =[]
        if 'custom_sections' in parsed_data:
            for cs in parsed_data['custom_sections']:
                # Retain ID if updating, otherwise give a new ID
                cid = cs.get('id', str(uuid.uuid4().hex))
                cs['id'] = cid
                custom_ids.append(f"custom_{cid}")
                
        st.session_state.r_data = parsed_data
        st.session_state.r_data['photo_bytes'] = preserved_photo
        
        # Logic to ensure the UI order list matches the newly merged data
        if not merge:
            st.session_state.section_order =['core_Summary', 'core_Education', 'core_Experience', 'core_Projects', 'core_Leadership'] + custom_ids + ['core_Skills']
        else:
            # If we merged and the AI created a NEW custom section, make sure we add it to the UI order!
            for cid_str in custom_ids:
                if cid_str not in st.session_state.section_order:
                    # Insert right before skills
                    idx = st.session_state.section_order.index('core_Skills') if 'core_Skills' in st.session_state.section_order else len(st.session_state.section_order)
                    st.session_state.section_order.insert(idx, cid_str)
                    
        return True
    except Exception as e:
        st.error(f"Failed to parse AI response: {e}")
        return False

def polish_bullet_with_ai(text):
    prompt = f"""Rewrite the following resume bullet points to be punchier, metric-driven, and follow the STAR method (Situation, Task, Action, Result). 
    Start each bullet with a strong action verb. Keep it to a concise bulleted list. 
    Format: Use asterisks for bolding key metrics/tools (e.g., "Increased revenue by **20%** using **Python**").
    
    Original Text:
    {text}
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        content = completion.choices[0].message.content.strip()
        return re.sub(r'```[a-zA-Z]*\n|```', '', content).strip()
    except Exception as e:
        st.error(f"AI Polish Failed: {e}")
        return text

# --- PDF GENERATOR (fpdf2) ---
def generate_harvard_pdf(data, settings):
    paper_w = 8.5 if settings['paper_size'] == "Letter" else 8.27
    paper_h = 11.0 if settings['paper_size'] == "Letter" else 11.69
    
    pdf = FPDF(unit="in", format=settings['paper_size'].lower())
    author_name = sanitize(data.get('name', 'Candidate'))
    pdf.set_title(f"{author_name} - Resume")
    pdf.set_author(author_name)
    pdf.set_auto_page_break(auto=True, margin=settings['margin'])
    pdf.add_page()
    
    margin = settings['margin']
    spacing = settings['spacing'] 
    base_font = settings['font_size']
    font_fam = settings['font_family']
    header_align = settings['header_align'][0] 
    accent_rgb = settings['accent_rgb']
    
    pdf.set_margins(left=margin, top=margin, right=margin)

    # ALIGNMENT GRID
    if settings.get('show_grid'):
        pdf.set_draw_color(200, 220, 255)
        pdf.set_font("Helvetica", "", 6)
        for i in range(1, 85): 
            x = i / 10.0
            pdf.line(x, 0, x, 11)
        for i in range(1, 110):
            y = i / 10.0
            pdf.line(0, y, 8.5, y)
        pdf.set_draw_color(0, 0, 0)

    # PHOTO HANDLING
    photo_h = 0
    if not settings['strict_mode'] and data.get('photo_bytes') and settings['photo_position'] != "Hide Photo":
        try:
            img = Image.open(io.BytesIO(data['photo_bytes']))
            p_w = settings['photo_size']
            photo_h = p_w * (img.height / img.width)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                img.convert('RGB').save(tmp.name, format="JPEG")
                tmp_path = tmp.name
                
            base_p_x = margin if settings['photo_position'] == "Top Left" else paper_w - margin - p_w
            if settings['photo_position'] == "Top Left": pdf.set_left_margin(margin + p_w + 0.2)
            else: pdf.set_right_margin(margin + p_w + 0.2)
                
            pdf.image(tmp_path, x=base_p_x + settings['photo_x_offset'], y=margin + settings['photo_y_offset'], w=p_w)
            os.remove(tmp_path)
            pdf.set_x(pdf.l_margin)
        except Exception as e:
            pass

    # Header
    pdf.set_text_color(*accent_rgb)
    pdf.set_font(font_fam, "B", settings['header_size'])
    pdf.cell(w=0, h=0.3, text=author_name, align=header_align, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    
    pdf.set_font(font_fam, "", base_font)
    contact_parts =[p for p in [data['address'], data['phone'], data['email'], clean_url(data.get('linkedin', ''))] if p.strip()]
    pdf.cell(w=0, h=0.2, text=sanitize("  |  ".join(contact_parts)), align=header_align, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(0.1)
    
    pdf.set_left_margin(margin)
    pdf.set_right_margin(margin)
    if photo_h > 0 and pdf.get_y() < (margin + settings['photo_y_offset'] + photo_h):
        pdf.set_y(margin + settings['photo_y_offset'] + photo_h + 0.1)

    def check_page_break(required_height):
        if pdf.get_y() > (paper_h - margin - required_height):
            pdf.add_page()
            
    def add_section_header(title):
        check_page_break(0.5)
        pdf.set_text_color(*accent_rgb)
        pdf.set_draw_color(*accent_rgb)
        pdf.set_font(font_fam, "B", base_font)
        pdf.cell(w=0, h=0.25, text=title.upper(), border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_draw_color(0, 0, 0)
        pdf.ln(0.05)
        
    def add_left_right(left_text, right_text, left_style, right_style):
        y_before = pdf.get_y()
        pdf.set_font(font_fam, left_style, base_font)
        pdf.cell(w=0, h=0.2, text=sanitize(left_text), align="L")
        if right_text:
            pdf.set_y(y_before)
            pdf.set_font(font_fam, right_style, base_font)
            pdf.cell(w=0, h=0.2, text=sanitize(right_text), align="R", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(0.2)

    def print_bullets(bullets_text):
        if not bullets_text.strip(): return
        bullets =[b.strip() for b in bullets_text.split('\n') if b.strip()]
        for bullet in bullets:
            bullet = sanitize(bullet.lstrip('-').lstrip('•').lstrip('*').strip())
            if not bullet: continue
            pdf.set_font(font_fam, "", base_font)
            pdf.cell(w=0.2, h=0.2 * spacing, text=chr(149), align="R") 
            orig_lmargin = pdf.l_margin
            pdf.set_left_margin(orig_lmargin + 0.25)
            pdf.set_x(orig_lmargin + 0.25)
            pdf.multi_cell(w=0, h=0.2 * spacing, text=bullet, markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.set_left_margin(orig_lmargin)
            pdf.set_x(orig_lmargin)

    # Render Sections
    for sec_key in settings['section_order']:
        
        # Summary Section
        if sec_key == 'core_Summary' and data.get('summary', '').strip():
            add_section_header(data.get('heading_summary', 'Professional Summary'))
            pdf.set_font(font_fam, "", base_font)
            pdf.multi_cell(w=0, h=0.2 * spacing, text=sanitize(data['summary']), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.1)

        # Education
        elif sec_key == 'core_Education' and any(ed['school'] for ed in data.get('education',[])):
            add_section_header(data.get('heading_education', 'Education'))
            for ed in data['education']:
                if not ed['school']: continue
                check_page_break(0.6)
                add_left_right(ed['school'], ed['location'], "B", "B")
                add_left_right(ed['degree'], ed['date'], "I", "I")
                if ed['details']:
                    pdf.set_font(font_fam, "", base_font)
                    pdf.multi_cell(w=0, h=0.2 * spacing, text=sanitize(ed['details']), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.1)

        # Experience / Leadership shared logic
        elif sec_key in ['core_Experience', 'core_Leadership']:
            list_key = 'experience' if sec_key == 'core_Experience' else 'leadership'
            name_key = 'company' if sec_key == 'core_Experience' else 'organization'
            if any(item.get(name_key) for item in data.get(list_key,[])):
                add_section_header(data.get(f'heading_{list_key}', list_key.capitalize()))
                for item in data[list_key]:
                    if not item.get(name_key): continue
                    check_page_break(0.6)
                    add_left_right(item[name_key], item.get('location', ''), "B", "B")
                    add_left_right(item.get('title', ''), item.get('date', ''), "I", "I")
                    print_bullets(item.get('bullets', ''))
                pdf.ln(0.1)

        # Projects
        elif sec_key == 'core_Projects' and any(p['title'] for p in data.get('projects',[])):
            add_section_header(data.get('heading_projects', 'Academic & Personal Projects'))
            for p in data['projects']:
                if not p['title']: continue
                check_page_break(0.6)
                add_left_right(p['title'], p.get('date', ''), "B", "B")
                if p.get('role'): add_left_right(p['role'], "", "I", "")
                print_bullets(p.get('bullets', ''))
            pdf.ln(0.1)

        # Skills
        elif sec_key == 'core_Skills' and any(data.get('skills', {}).values()):
            add_section_header(data.get('heading_skills', 'Skills & Interests'))
            pdf.set_font(font_fam, "", base_font)
            for label, key in[("Technical", "technical"), ("Languages", "languages"), ("Interests", "interests")]:
                val = data['skills'].get(key, '')
                if val:
                    check_page_break(0.2)
                    pdf.set_font(font_fam, "B", base_font)
                    label_width = pdf.get_string_width(label + ": ")
                    pdf.cell(w=label_width, h=0.2 * spacing, text=sanitize(label + ": "))
                    pdf.set_font(font_fam, "", base_font)
                    pdf.multi_cell(w=0, h=0.2 * spacing, text=sanitize(val), markdown=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.1)

        # Custom Sections
        elif sec_key.startswith('custom_'):
            cid = sec_key.split('_')[1]
            c_sec = next((cs for cs in data.get('custom_sections',[]) if cs.get('id') == cid), None)
            if c_sec and c_sec['title'].strip() and c_sec['content'].strip():
                add_section_header(c_sec['title'])
                pdf.set_font(font_fam, "", base_font)
                pdf.multi_cell(w=0, h=0.2 * spacing, text=sanitize(c_sec['content']), markdown=True, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(0.1)

    return bytes(pdf.output()), pdf.page_no()

# --- STREAMLIT UI SETUP ---
st.set_page_config(page_title="Harvard Resume Builder", layout="wide")

# Init Session State
if 'r_data' not in st.session_state:
    st.session_state.r_data = {
        'name': '', 'address': '', 'phone': '', 'email': '', 'linkedin': '', 'summary': '',
        'heading_summary': 'Professional Summary', 'heading_education': 'Education', 
        'heading_experience': 'Experience', 'heading_projects': 'Projects', 
        'heading_leadership': 'Leadership & Extracurriculars', 'heading_skills': 'Skills',
        'education':[{'school': '', 'location': '', 'degree': '', 'date': '', 'details': ''}],
        'experience':[{'company': '', 'location': '', 'title': '', 'date': '', 'bullets': ''}],
        'projects':[], 'leadership':[],
        'skills': {'technical': '', 'languages': '', 'interests': ''},
        'custom_sections':[], 'photo_bytes': None
    }

if 'section_order' not in st.session_state:
    st.session_state.section_order =['core_Summary', 'core_Education', 'core_Experience', 'core_Projects', 'core_Leadership', 'core_Skills']

if 'pdf_preview_bytes' not in st.session_state: st.session_state.pdf_preview_bytes = None
if 'page_count_warning' not in st.session_state: st.session_state.page_count_warning = False

# --- SIDEBAR: SAVE / LOAD SYSTEM ---
with st.sidebar:
    st.header("💾 Save / Load Project")
    st.info("Don't lose your progress! Save your resume data to your computer, and load it later.")
    
    clean_data = {k: v for k, v in st.session_state.r_data.items() if k != 'photo_bytes'}
    json_str = json.dumps(clean_data, indent=2)
    st.download_button("⬇️ Download Resume Data (.json)", data=json_str, file_name="my_resume_data.json", mime="application/json")
    st.divider()
    
    uploaded_json = st.file_uploader("⬆️ Load Resume Data (.json)", type="json")
    if uploaded_json is not None:
        if st.button("Load Data", type="primary"):
            loaded_data = json.load(uploaded_json)
            preserved_photo = st.session_state.r_data.get('photo_bytes')
            st.session_state.r_data.update(loaded_data)
            st.session_state.r_data['photo_bytes'] = preserved_photo
            
            # Ensure custom sections from JSON are added to order list if missing
            for cs in st.session_state.r_data.get('custom_sections',[]):
                if f"custom_{cs['id']}" not in st.session_state.section_order:
                    st.session_state.section_order.append(f"custom_{cs['id']}")
                    
            st.success("Resume loaded successfully!")
            st.rerun()

st.title("🎓 The Ultimate Harvard Resume Builder")

# --- STEP 1: IMPORT ---
st.markdown("### 📄 Step 1: Import Your Data")
col_pdf, col_text = st.columns(2)
with col_pdf: uploaded_file = st.file_uploader("1️⃣ Upload Old Resume (PDF)", type="pdf")
with col_text: pasted_text = st.text_area("2️⃣ Or Paste Text (LinkedIn, Job Spec, or Additions)", height=100)

def process_input(merge):
    combined = ""
    
    if uploaded_file:
        try:
            uploaded_file.seek(0) # BUG FIX: Ensure the file is read from the beginning every time!
            for page in PyPDF2.PdfReader(uploaded_file).pages: 
                combined += page.extract_text() + "\n"
        except Exception as e:
            st.error(f"Error reading PDF: {e}")
            
    if pasted_text: 
        # BUG FIX: Clearly label the pasted text so the AI doesn't ignore it during a merge!
        combined += "\n\n--- NEW ADDITIONAL INSTRUCTIONS / TEXT ---\n" + pasted_text 
        
    if combined.strip():
        with st.spinner("⚡ Groq AI parsing data..."):
            if auto_fill_with_ai(combined, merge=merge): 
                st.success("Done!")
                st.rerun()
    else: 
        st.warning("Upload or paste text first.")

col_b1, col_b2, _ = st.columns([1, 1, 2])
with col_b1:
    if st.button("✨ Generate Fresh Resume", type="primary"): process_input(False)
with col_b2:
    if st.button("➕ Merge with Current"): process_input(True)

st.divider()

# --- STEP 2: EDITING ---
st.markdown("### 📝 Step 2: Edit Inside Categories")
tabs = st.tabs(["👤 Info & Summary", "🎓 Education", "💼 Experience", "🚀 Projects", "🤝 Leadership", "🛠️ Skills", "⭐ Custom"])

def move_item(lst, idx, dir):
    if dir == 'up' and idx > 0: lst[idx], lst[idx-1] = lst[idx-1], lst[idx]
    if dir == 'down' and idx < len(lst)-1: lst[idx], lst[idx+1] = lst[idx+1], lst[idx]

with tabs[0]: # Info & Summary
    c_text, c_img = st.columns([2, 1])
    with c_text:
        st.session_state.r_data['name'] = st.text_input("Full Name", st.session_state.r_data['name'])
        c1, c2 = st.columns(2)
        st.session_state.r_data['address'] = c1.text_input("City, State", st.session_state.r_data['address'])
        st.session_state.r_data['phone'] = c2.text_input("Phone", st.session_state.r_data['phone'])
        st.session_state.r_data['email'] = c1.text_input("Email", st.session_state.r_data['email'])
        st.session_state.r_data['linkedin'] = c2.text_input("LinkedIn URL", st.session_state.r_data['linkedin'])
    with c_img:
        photo = st.file_uploader("Profile Photo (Creative Mode Only)", type=["jpg", "png", "jpeg"])
        if photo: st.session_state.r_data['photo_bytes'] = photo.getvalue()
    
    st.divider()
    st.session_state.r_data['heading_summary'] = st.text_input("Summary Section Title", st.session_state.r_data.get('heading_summary', 'Professional Summary'))
    st.session_state.r_data['summary'] = st.text_area("Professional Summary Text", st.session_state.r_data.get('summary', ''), height=100)

with tabs[1]: # Edu
    st.session_state.r_data['heading_education'] = st.text_input("Education Section Title", st.session_state.r_data.get('heading_education', 'Education'), key='h_edu')
    for i, ed in enumerate(st.session_state.r_data['education']):
        with st.expander(f"{ed.get('school', 'New School')} - {ed.get('degree', '')}", expanded=True):
            cu, cd, cx, _ = st.columns([1,1,1,7])
            if cu.button("⬆️", key=f"eu_{i}"): move_item(st.session_state.r_data['education'], i, 'up'); st.rerun()
            if cd.button("⬇️", key=f"ed_{i}"): move_item(st.session_state.r_data['education'], i, 'down'); st.rerun()
            if cx.button("🗑️", key=f"ex_{i}"): st.session_state.r_data['education'].pop(i); st.rerun()
            c1, c2 = st.columns(2)
            ed['school'] = c1.text_input("School", ed.get('school', ''), key=f"es_{i}")
            ed['location'] = c2.text_input("Location", ed.get('location', ''), key=f"el_{i}")
            ed['degree'] = c1.text_input("Degree", ed.get('degree', ''), key=f"edg_{i}")
            ed['date'] = c2.text_input("Date", ed.get('date', ''), key=f"edt_{i}")
            ed['details'] = st.text_input("GPA / Honors", ed.get('details', ''), key=f"eh_{i}")
    if st.button("➕ Add School"): st.session_state.r_data['education'].append({}); st.rerun()

with tabs[2]: # Experience
    st.session_state.r_data['heading_experience'] = st.text_input("Experience Section Title", st.session_state.r_data.get('heading_experience', 'Experience'), key='h_exp')
    for i, exp in enumerate(st.session_state.r_data['experience']):
        with st.expander(f"{exp.get('company', 'New Job')} - {exp.get('title', '')}", expanded=True):
            cu, cd, cx, _ = st.columns([1,1,1,7])
            if cu.button("⬆️", key=f"xu_{i}"): move_item(st.session_state.r_data['experience'], i, 'up'); st.rerun()
            if cd.button("⬇️", key=f"xdn_{i}"): move_item(st.session_state.r_data['experience'], i, 'down'); st.rerun()
            if cx.button("🗑️", key=f"xx_{i}"): st.session_state.r_data['experience'].pop(i); st.rerun()
            c1, c2 = st.columns(2)
            exp['company'] = c1.text_input("Company", exp.get('company', ''), key=f"xc_{i}")
            exp['location'] = c2.text_input("Location", exp.get('location', ''), key=f"xl_{i}")
            exp['title'] = c1.text_input("Title", exp.get('title', ''), key=f"xt_{i}")
            exp['date'] = c2.text_input("Date", exp.get('date', ''), key=f"xdt_{i}")
            exp['bullets'] = st.text_area("Bullets (Use **text** for bold)", exp.get('bullets', ''), height=120, key=f"xb_{i}")
            if st.button("✨ Polish Bullets (AI)", key=f"xai_{i}"):
                with st.spinner("Rewriting using STAR method..."):
                    exp['bullets'] = polish_bullet_with_ai(exp['bullets'])
                    st.rerun()
    if st.button("➕ Add Job"): st.session_state.r_data['experience'].append({}); st.rerun()

with tabs[3]: # Projects
    st.session_state.r_data['heading_projects'] = st.text_input("Projects Section Title", st.session_state.r_data.get('heading_projects', 'Projects'), key='h_proj')
    for i, p in enumerate(st.session_state.r_data['projects']):
        with st.expander(f"{p.get('title', 'New Project')}", expanded=True):
            cu, cd, cx, _ = st.columns([1,1,1,7])
            if cu.button("⬆️", key=f"pu_{i}"): move_item(st.session_state.r_data['projects'], i, 'up'); st.rerun()
            if cd.button("⬇️", key=f"pdn_{i}"): move_item(st.session_state.r_data['projects'], i, 'down'); st.rerun()
            if cx.button("🗑️", key=f"px_{i}"): st.session_state.r_data['projects'].pop(i); st.rerun()
            c1, c2 = st.columns(2)
            p['title'] = c1.text_input("Project Name", p.get('title', ''), key=f"pt_{i}")
            p['date'] = c2.text_input("Date", p.get('date', ''), key=f"pdt_{i}")
            p['role'] = c1.text_input("Role / Tech Stack", p.get('role', ''), key=f"pr_{i}")
            p['bullets'] = st.text_area("Bullets", p.get('bullets', ''), height=100, key=f"pb_{i}")
            if st.button("✨ Polish Bullets (AI)", key=f"pai_{i}"):
                with st.spinner("Rewriting..."):
                    p['bullets'] = polish_bullet_with_ai(p['bullets'])
                    st.rerun()
    if st.button("➕ Add Project"): st.session_state.r_data['projects'].append({}); st.rerun()

with tabs[4]: # Leadership
    st.session_state.r_data['heading_leadership'] = st.text_input("Leadership Section Title", st.session_state.r_data.get('heading_leadership', 'Leadership & Extracurriculars'), key='h_lead')
    for i, l in enumerate(st.session_state.r_data['leadership']):
        with st.expander(f"{l.get('organization', 'New Org')}", expanded=True):
            cu, cd, cx, _ = st.columns([1,1,1,7])
            if cu.button("⬆️", key=f"lu_{i}"): move_item(st.session_state.r_data['leadership'], i, 'up'); st.rerun()
            if cd.button("⬇️", key=f"ldn_{i}"): move_item(st.session_state.r_data['leadership'], i, 'down'); st.rerun()
            if cx.button("🗑️", key=f"lx_{i}"): st.session_state.r_data['leadership'].pop(i); st.rerun()
            c1, c2 = st.columns(2)
            l['organization'] = c1.text_input("Organization", l.get('organization', ''), key=f"lo_{i}")
            l['location'] = c2.text_input("Location", l.get('location', ''), key=f"ll_{i}")
            l['title'] = c1.text_input("Title/Role", l.get('title', ''), key=f"lt_{i}")
            l['date'] = c2.text_input("Date", l.get('date', ''), key=f"ldt_{i}")
            l['bullets'] = st.text_area("Bullets", l.get('bullets', ''), height=100, key=f"lb_{i}")
            if st.button("✨ Polish Bullets (AI)", key=f"lai_{i}"):
                with st.spinner("Rewriting..."):
                    l['bullets'] = polish_bullet_with_ai(l['bullets'])
                    st.rerun()
    if st.button("➕ Add Leadership"): st.session_state.r_data['leadership'].append({}); st.rerun()

with tabs[5]: # Skills
    st.session_state.r_data['heading_skills'] = st.text_input("Skills Section Title", st.session_state.r_data.get('heading_skills', 'Skills & Interests'))
    sk = st.session_state.r_data['skills']
    sk['technical'] = st.text_area("Technical Skills (Use commas)", sk.get('technical', ''))
    sk['languages'] = st.text_input("Languages", sk.get('languages', ''))
    sk['interests'] = st.text_input("Interests", sk.get('interests', ''))

with tabs[6]: # Custom
    st.info("You can add extra blocks like 'Certifications' or 'Publications' here.")
    for i, sec in enumerate(st.session_state.r_data.get('custom_sections',[])):
        with st.expander(f"Custom: {sec.get('title', 'Unnamed Section')}", expanded=True):
            cu, cd, cx, _ = st.columns([1,1,1,7])
            if cu.button("⬆️", key=f"cu_{i}"): move_item(st.session_state.r_data['custom_sections'], i, 'up'); st.rerun()
            if cd.button("⬇️", key=f"cdn_{i}"): move_item(st.session_state.r_data['custom_sections'], i, 'down'); st.rerun()
            if cx.button("🗑️", key=f"cx_{i}"):
                if f"custom_{sec['id']}" in st.session_state.section_order:
                    st.session_state.section_order.remove(f"custom_{sec['id']}")
                st.session_state.r_data['custom_sections'].pop(i)
                st.rerun()
            sec['title'] = st.text_input("Section Header", sec.get('title', ''), key=f"ct_{i}")
            sec['content'] = st.text_area("Content", sec.get('content', ''), key=f"cc_{i}")
    if st.button("➕ Add Custom Block"):
        nid = str(uuid.uuid4().hex)
        st.session_state.r_data['custom_sections'].append({'id': nid, 'title': '', 'content': ''})
        st.session_state.section_order.append(f'custom_{nid}')
        st.rerun()

st.divider()

# --- STEP 3: REORDER SECTIONS ---
st.markdown("### 🗂️ Step 3: Global Category Order")
st.info("Use the arrows to reorder how the sections appear on your final PDF.")
for i, sec_key in enumerate(st.session_state.section_order):
    c1, c2, c3 = st.columns([1, 1, 12])
    if c1.button("⬆️", key=f"gu_{i}"): move_item(st.session_state.section_order, i, "up"); st.rerun()
    if c2.button("⬇️", key=f"gd_{i}"): move_item(st.session_state.section_order, i, "down"); st.rerun()
    
    # Clearly label the blocks
    if sec_key.startswith('core_'): 
        name = st.session_state.r_data.get(f"heading_{sec_key.split('_')[1].lower()}", sec_key.split('_')[1])
    elif sec_key.startswith('custom_'):
        cs = next((c for c in st.session_state.r_data['custom_sections'] if c['id'] == sec_key.split('_')[1]), None)
        if cs: 
            name = f"Custom Section: {cs.get('title', '[Unnamed]')}"
        else:
            name = "Unknown Block"
            
    c3.markdown(f"**{name}**")

st.divider()

# --- STEP 4: EXPORT & PDF ---
st.markdown("### 👁️ Step 4: Alignment Studio & Export")

st_strict_mode = st.toggle("🎓 Strict Harvard Compliance Mode", value=True, 
                           help="Locks formatting to standard US Corporate / Ivy League standards (Black & White, Times font, No Photo).")

if st_strict_mode:
    st.info("🔒 Strict Mode ON: Formatting is locked for maximum ATS compliance and professionalism.")
    settings = {
        'strict_mode': True, 'paper_size': 'Letter', 'font_family': 'Times', 'header_align': 'Center',
        'margin': 0.75, 'font_size': 11, 'header_size': 16, 'spacing': 1.0,
        'photo_position': 'Hide Photo', 'photo_size': 0, 'photo_x_offset': 0, 'photo_y_offset': 0,
        'accent_rgb': (0,0,0), 'show_grid': False, 'section_order': st.session_state.section_order
    }
else:
    st.warning("🎨 Creative Mode ON: Best for European CVs or design portfolios. (Photos are generally rejected by US Corporate ATS).")
    with st.expander("🎨 Advanced Design Settings", expanded=True):
        col_set1, col_set2, col_set3, col_set4 = st.columns(4)
        with col_set1:
            paper_size = st.selectbox("Paper Size",["Letter", "A4"])
            font_family = st.selectbox("Font Style",["Times", "Arial", "Helvetica", "Courier"])
            header_align = st.selectbox("Header Align",["Center", "Left", "Right"])
        with col_set2:
            margin_size = st.slider("Margins (in)", 0.3, 1.5, 0.75, 0.05)
            font_size = st.slider("Base Font Size", 9, 12, 11, 1)
            header_size = st.slider("Header Text Size", 12, 24, 16, 1)
        with col_set3:
            line_spacing = st.slider("Line Spacing", 0.8, 1.5, 1.0, 0.1)
            photo_pos = st.selectbox("Photo Pos",["Top Right", "Top Left", "Hide Photo"])
            photo_size = st.slider("Photo Width", 0.5, 2.0, 1.0, 0.1)
            px_off = st.slider("Move Left/Right", -3.0, 3.0, 0.0, 0.05)
            py_off = st.slider("Move Up/Down", -3.0, 3.0, 0.0, 0.05)
        with col_set4:
            show_grid = st.toggle("📏 Show Ruler Grid", False)
            accent_rgb = hex_to_rgb(st.color_picker("Accent Color", "#000000"))
            
    settings = {
        'strict_mode': False, 'paper_size': paper_size, 'font_family': font_family, 'header_align': header_align,
        'margin': margin_size, 'font_size': font_size, 'header_size': header_size, 'spacing': line_spacing,
        'photo_position': photo_pos, 'photo_size': photo_size, 'photo_x_offset': px_off, 'photo_y_offset': py_off,
        'accent_rgb': accent_rgb, 'show_grid': show_grid, 'section_order': st.session_state.section_order
    }

col_gen, col_dl = st.columns([1, 4])

with col_gen:
    if st.button("🔄 Update Live Preview", type="primary", use_container_width=True):
        pdf_bytes, pages = generate_harvard_pdf(st.session_state.r_data, settings)
        st.session_state.pdf_preview_bytes = pdf_bytes
        st.session_state.page_count_warning = pages > 1

if st.session_state.page_count_warning:
    st.error("🚨 WARNING: Your resume is longer than ONE PAGE! For strict Harvard/Finance standards, you should shorten your bullet points or lower your font size/margins.")

if st.session_state.pdf_preview_bytes:
    with col_dl:
        st.download_button(
            label="⬇️ Download Final PDF",
            data=st.session_state.pdf_preview_bytes,
            file_name="Harvard_Style_Resume.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
    b64_pdf = base64.b64encode(st.session_state.pdf_preview_bytes).decode('utf-8')
    canvas_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js"></script>
        <style>
            body {{ background-color: #2e3033; margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; }}
            canvas {{ box-shadow: 0px 4px 15px rgba(0,0,0,0.5); max-width: 100%; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <div id="pdf-container"></div>
        <script>
            var binaryData = atob("{b64_pdf}");
            var pdfjsLib = window['pdfjs-dist/build/pdf'];
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

            var loadingTask = pdfjsLib.getDocument({{data: binaryData}});
            loadingTask.promise.then(function(pdf) {{
                for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {{
                    pdf.getPage(pageNum).then(function(page) {{
                        var scale = 1.5; 
                        var viewport = page.getViewport({{scale: scale}});
                        var canvas = document.createElement('canvas');
                        var context = canvas.getContext('2d');
                        canvas.height = viewport.height;
                        canvas.width = viewport.width;
                        document.getElementById('pdf-container').appendChild(canvas);
                        var renderContext = {{ canvasContext: context, viewport: viewport }};
                        page.render(renderContext);
                    }});
                }}
            }});
        </script>
    </body>
    </html>
    """
    components.html(canvas_html, height=900, scrolling=True)
