import streamlit as st
import json
from datetime import date, datetime, timedelta
import hashlib
import random
import pandas as pd
import xlsxwriter

st.set_page_config(page_title="Planning Médical - Planning des Médecins", layout="centered")
st.title("🩺 Planning des Médecins")

DATA_FILE = "medecins_data.json"

# Formatage manuel en français
jours_fr = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
mois_fr = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]

def format_date_fr(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except:
        return date_str
    return f"{jours_fr[d.weekday()]} {d.day} {mois_fr[d.month-1]} {d.year}"

# Générer une couleur stable à partir du nom
def couleur_pour_nom(nom):
    h = hashlib.md5(nom.encode()).hexdigest()
    return f"#{h[:6]}"

# Charger les données
try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
except FileNotFoundError:
    data = {"dates_interdites_globales": [], "medecins": [], "planning": {}}

# Initialisation
data.setdefault("dates_interdites_globales", [])
data.setdefault("planning", {})
for m in data.setdefault("medecins", []):
    m.setdefault("vacances", [])

# Callback pour confirmation
def confirm_action(flag_key):
    st.session_state[flag_key] = True

# Vérifier si un médecin est disponible

def is_available(day, med):
    for vac in med['vacances']:
        start = datetime.strptime(vac[0], "%Y-%m-%d").date()
        end = datetime.strptime(vac[1], "%Y-%m-%d").date()
        if start <= day <= end:
            return False
    return True

# Affectation aléatoire simple des rôles disponibles
roles_journaliers = ["HDL", "Hospit"]

def assign_roles(day):
    jour_str = str(day)
    if jour_str not in data['planning']:
        data['planning'][jour_str] = {}
    disponibles = [med['nom'] for med in data['medecins'] if is_available(day, med)]
    random.shuffle(disponibles)
    for i, role in enumerate(roles_journaliers):
        if i < len(disponibles):
            data['planning'][jour_str][role] = disponibles[i]

def assign_roles_smart(start_date, weeks=52, block_days=14):
    jours = [start_date + timedelta(days=i) for i in range(weeks * 7)]
    medecins = [m['nom'] for m in data['medecins']]
    roles = ["Hospit1", "Hospit2", "HDL1", "HDL2", "HDM1", "HDM2"]
    all_roles = roles + ["Consult", "HDL_Samedi", "Hospit_Samedi", "Hospit_Dimanche"]

    indispo = {m['nom']: set() for m in data['medecins']}
    for m in data['medecins']:
        for v in m['vacances']:
            d1 = datetime.strptime(v[0], "%Y-%m-%d").date()
            d2 = datetime.strptime(v[1], "%Y-%m-%d").date()
            for j in range((d2 - d1).days + 1):
                indispo[m['nom']].add(d1 + timedelta(days=j))
        for di in data["dates_interdites_globales"]:
            indispo[m['nom']].add(datetime.strptime(di, "%Y-%m-%d").date())

    planning = {}
    role_rotations = {role: medecins[:] for role in roles}
    for role in roles:
        random.shuffle(role_rotations[role])

    used_per_day = {}

    # Affectation des blocs de 14 jours pour chaque rôle prioritaire
    for role in roles:
        rotation = role_rotations[role]
        rot_index = 0
        day_index = 0
        while day_index < len(jours):
            bloc = [d for d in jours[day_index:day_index + block_days] if d.weekday() < 5]
            for _ in range(len(rotation)):
                cand = rotation[rot_index % len(rotation)]
                if all(
                    d not in indispo[cand] and
                    cand not in used_per_day.get(str(d), set())
                    for d in bloc
                ):
                    for d in bloc:
                        jour_str = str(d)
                        planning.setdefault(jour_str, {})[role] = cand
                        used_per_day.setdefault(jour_str, set()).add(cand)
                    rot_index += 1
                    break
                rot_index += 1
            day_index += block_days
    dernier_weekend = {m: None for m in medecins}

    # Affectation des week-ends
    for i in range(len(jours) - 1):
        d = jours[i]
        if d.weekday() == 5:  # Samedi
            samedi = d
            dimanche = d + timedelta(days=1)
            jour_s = str(samedi)
            jour_d = str(dimanche)

            dispo = [m for m in medecins
                     if samedi not in indispo[m]
                     and dimanche not in indispo[m]
                     and m not in used_per_day.get(jour_s, set())
                     and m not in used_per_day.get(jour_d, set())
                     and (dernier_weekend[m] is None or (samedi - dernier_weekend[m]).days >= 14)
                    ]
            random.shuffle(dispo)

            if len(dispo) >= 2:
                m_hdl = dispo[0]
                m_hospit = dispo[1]

                planning.setdefault(jour_s, {})["HDL_Samedi"] = m_hdl
                planning[jour_s]["Hospit_Samedi"] = m_hospit
                planning.setdefault(jour_d, {})["Hospit_Dimanche"] = m_hospit

                used_per_day.setdefault(jour_s, set()).update([m_hdl, m_hospit])
                used_per_day.setdefault(jour_d, set()).add(m_hospit)

                # On note que ces médecins ont travaillé ce week-end
                dernier_weekend[m_hdl] = samedi
                dernier_weekend[m_hospit] = samedi

    # Affectation des Consult pour les jours où médecins sont dispo mais non affectés
    for d in jours:
        if d.weekday() >= 5:
            continue  # on saute les week-ends
        jour_str = str(d)
        already = used_per_day.get(jour_str, set())
        for m in medecins:
            if m not in already and d not in indispo[m]:
                planning.setdefault(jour_str, {})
                planning[jour_str].setdefault("Consult", [])
                planning[jour_str]["Consult"].append(m)
                used_per_day.setdefault(jour_str, set()).add(m)

    data['planning'] = planning
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)



def ajouter_vacances(med, new_start, new_end, depart, retour):
    # Vérification chevauchement
    overlap = False
    for ev in med['vacances']:
        ev_start = datetime.strptime(ev[0], "%Y-%m-%d").date()
        ev_end = datetime.strptime(ev[1], "%Y-%m-%d").date()
        if new_start <= ev_end and new_end >= ev_start:
            overlap = True
            break

    # Vérification dates interdites globales
    interdit = any(
        str(new_start + timedelta(days=i)) in data["dates_interdites_globales"]
        for i in range((new_end - new_start).days + 1)
    )

    if interdit:
        st.warning("⚠️ Impossible de poser un congé sur une ou plusieurs dates interdites globalement.")
    elif overlap:
        st.warning("⚠️ Plage en chevauchement avec un autre souhait existant.")
    else:
        med['vacances'].append([str(new_start), str(new_end), depart, retour])
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        st.success("✅ Demande ajoutée.")
        st.rerun()


# Section 1: Dates globales interdites
st.subheader("🚫 Dates interdites")
new_date = st.date_input("Ajouter une date où les congés seront interdits", date.today(), key="new_date_input")
if st.button("➕ Ajouter"):
    ds = str(new_date)
    if ds not in data["dates_interdites_globales"]:
        data["dates_interdites_globales"].append(ds)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        st.success(f"✅ Date {format_date_fr(ds)} ajoutée.")
    else:
        st.warning("⚠️ Date déjà présente.")

if data["dates_interdites_globales"]:
    st.markdown("### 📌 Dates interdites :")
    for idx, d in enumerate(sorted(data["dates_interdites_globales"])):
        flag = f"glob_flag_{idx}"
        st.session_state.setdefault(flag, False)
        col1, col2 = st.columns([4,1])
        with col1:
            st.write(f"🔒 {format_date_fr(d)}")
        with col2:
            if not st.session_state[flag]:
                st.button("❌", key=f"del_glob_{idx}", on_click=confirm_action, args=(flag,))
            else:
                if st.button("Confirmer", key=f"conf_glob_{idx}"):
                    data["dates_interdites_globales"].remove(d)
                    with open(DATA_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4)
                    st.success(f"🚫 supprimée : {format_date_fr(d)}")
                    st.session_state[flag] = False
                    st.rerun()

st.markdown("---")

# Section 2: Médecins
st.subheader("➕ Ajouter un médecin")
with st.form("form_add_med"):
    nom = st.text_input("Nom du médecin", key="input_nom")
    if st.form_submit_button("Ajouter"):
        name = nom.strip()
        if not name:
            st.warning("⚠️ Nom vide.")
        elif any(m['nom'].lower()==name.lower() for m in data['medecins']):
            st.warning(f"⚠️ '{name}' existe.")
        else:
            data['medecins'].append({'nom':name, 'vacances':[]})
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            st.success(f"✅ {name} ajouté.")
            st.rerun()

if data['medecins']:
    st.markdown("### 📋 Médecins :")
    for i, med in enumerate(data['medecins']):
        flag = f"med_flag_{i}"
        st.session_state.setdefault(flag, False)
        col1, col2 = st.columns([4,1])
        with col1:
            st.write(f"- {med['nom']}")
        with col2:
            if not st.session_state[flag]:
                st.button("❌", key=f"del_med_{i}", on_click=confirm_action, args=(flag,))
            else:
                if st.button("Confirmer", key=f"conf_med_{i}"):
                    data['medecins'].pop(i)
                    with open(DATA_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4)
                    st.success(f"🚫 {med['nom']} supprimé.")
                    st.session_state[flag] = False
                    st.rerun()

st.markdown("---")

# Section 3: Vacances et demi-journées
st.subheader("📅 Souhaits de vacances")
if data['medecins']:
    choix = st.selectbox("Médecin", [m['nom'] for m in data['medecins']])
    med = next(m for m in data['medecins'] if m['nom']==choix)
    vac_range = st.date_input("Du - Au", value=(date.today(), date.today()), key="vac_add_range")
    col_dep, col_ret = st.columns(2)
    with col_dep:
        depart = st.selectbox("Partir", ["Matin","Midi"], key="vac_depart")
    with col_ret:
        retour = st.selectbox("Revenir", ["Midi","Soir"], key="vac_retour", index=1)
    if st.button("Ajouter souhait", key="btn_add_vac"):
        new_start, new_end = vac_range[0], vac_range[1]
        ajouter_vacances(med, new_start, new_end, depart, retour)

        overlap = False
        for ev in med['vacances']:
            ev_start = datetime.strptime(ev[0], "%Y-%m-%d").date()
            ev_end = datetime.strptime(ev[1], "%Y-%m-%d").date()
            if new_start <= ev_end and new_end >= ev_start:
                overlap = True
                break
        interdit = any(
            str(new_start + timedelta(days=i)) in data["dates_interdites_globales"]
            for i in range((new_end - new_start).days + 1)
        )
        if interdit:
            st.warning("⚠️ Impossible de poser un congé sur une ou plusieurs dates interdites globalement.")
        elif overlap:
            st.warning("⚠️ Plage en chevauchement avec un autre souhait existant.")
        else:
            med['vacances'].append([str(new_start), str(new_end), depart, retour])
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            st.success("✅ Demande ajoutée.")
            st.rerun()
    if med['vacances']:
        st.markdown(f"#### Souhaits pour {choix} :")
        for j, v in enumerate(med['vacances']):
            dep, ret = (v[2], v[3]) if len(v)>=4 else ("Matin","Soir")
            disp_dep = "Après-midi" if dep=="Midi" else dep
            disp_ret = "Matin" if ret=="Midi" else ret
            if v[0]==v[1] and dep=="Matin" and ret=="Soir":
                desc = format_date_fr(v[0])
            elif v[0]==v[1]:
                parts = []
                if dep!="Matin": parts.append(disp_dep)
                if ret!="Soir": parts.append(disp_ret)
                desc = f"{format_date_fr(v[0])} ({', '.join(parts)})"
            else:
                if dep=="Matin" and ret=="Soir":
                    desc = f"{format_date_fr(v[0])} → {format_date_fr(v[1])}"
                else:
                    desc = f"{format_date_fr(v[0])} → {format_date_fr(v[1])} (Départ: {disp_dep}, Retour: {disp_ret})"
            flag = f"vac_flag_{j}"
            st.session_state.setdefault(flag, False)
            col1, col2 = st.columns([4,1])
            with col1:
                st.write(desc)
            with col2:
                if not st.session_state[flag]:
                    st.button("❌", key=f"del_vac_{j}", on_click=confirm_action, args=(flag,))
                else:
                    if st.button("Confirmer", key=f"conf_vac_{j}"):
                        med['vacances'].pop(j)
                        with open(DATA_FILE, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=4)
                        st.success("🚫 Souhait supprimé.")
                        st.session_state[flag] = False
                        st.rerun()
else:
    st.info("Ajoutez un médecin pour continuer.")

# Récapitulatif
st.markdown("---")
st.subheader("📅 Récapitulatif des demandes de congés")
for m in data['medecins']:
    if m['vacances']:
        items=[]
        for v in m['vacances']:
            dep, ret = (v[2], v[3]) if len(v)>=4 else ("Matin","Soir")
            disp_dep = "Après-midi" if dep=="Midi" else dep
            disp_ret = "Matin" if ret=="Midi" else ret
            if v[0]==v[1] and dep=="Matin" and ret=="Soir":
                items.append(format_date_fr(v[0]))
            elif v[0]==v[1]:
                parts=[]
                if dep!="Matin": parts.append(disp_dep)
                if ret!="Soir": parts.append(disp_ret)
                items.append(f"{format_date_fr(v[0])} ({', '.join(parts)})")
            else:
                if dep=="Matin" and ret=="Soir":
                    items.append(f"{format_date_fr(v[0])} → {format_date_fr(v[1])}")
                else:
                    items.append(f"{format_date_fr(v[0])} → {format_date_fr(v[1])} (Départ: {disp_dep}, Retour: {disp_ret})")
        st.write(f"**{m['nom']}** : {', '.join(items)}")

# Section 4: Planning simplifié
st.markdown("---")
st.subheader("🗓️ Planning annuel simplifié (12 prochains mois)")

def render_yearly_calendar(start_date):
    st.markdown("""
    <style>
    table, th, td {
        border-collapse: collapse;
        padding: 6px;
        font-size: 12px;
        vertical-align: top;
    }
    .cell-wrapper {
        position: relative;
        min-height: 250px;
        height: 250px;
        width: 150px;
        min-width: 150px;
        padding-bottom: 4px;
    }
    .day-number {
        position: relative;
        top: 2px;
        left: 4px;
        font-weight: bold;
        font-size: 10px;
    }
    .cell-content {
        margin-top: 14px;
        font-size: 15px;
    }
    </style>
    """, unsafe_allow_html=True)


    html = ""
    for m in range(12):
        current_month = (start_date.month + m - 1) % 12 + 1
        current_year = start_date.year + ((start_date.month + m - 1) // 12)
        first_day = date(current_year, current_month, 1)
        html += f"<h4>{mois_fr[current_month - 1].capitalize()} {current_year}</h4>"
        html += "<table><tr>" + ''.join(f"<th>{j.capitalize()}</th>" for j in jours_fr) + "</tr><tr>"
        start_weekday = first_day.weekday()
        html += "<td></td>" * start_weekday
        day = first_day
        while day.month == current_month:
            jour_str = str(day)
            entries = []
            if jour_str in data['planning']:
                for role, name in data['planning'][jour_str].items():
                    if isinstance(name, list):
                        for n in name:
                            color = couleur_pour_nom(n)
                            entries.append(f"<div style='color:{color};'>{n} ({role})</div>")
                    else:
                        color = couleur_pour_nom(name)
                        entries.append(f"<div style='color:{color};'>{name} ({role})</div>")
            height = 20 + 14 * len(entries)
            cell_html = f"<div class='cell-wrapper' style='height:{height}px;'><div class='day-number'>{day.day}</div><div class='cell-content'>{''.join(entries)}</div></div>"
            html += f"<td>{cell_html}</td>"
            if day.weekday() == 6:
                html += "</tr><tr>"
            day += timedelta(days=1)
        html += "</tr></table><br>"
    st.markdown(html, unsafe_allow_html=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

if st.button("📅 Générer planning intelligent"):
    assign_roles_smart(date.today())
    render_yearly_calendar(date.today())

st.markdown("---")
st.subheader("📤 Exporter le planning")

# Transformer le planning en DataFrame
planning_liste = []
for jour, roles in data['planning'].items():
    for role, personne in roles.items():
        if isinstance(personne, list):
            for p in personne:
                planning_liste.append({"Date": jour, "Rôle": role, "Médecin": p})
        else:
            planning_liste.append({"Date": jour, "Rôle": role, "Médecin": personne})

df_planning = pd.DataFrame(planning_liste).sort_values(by="Date")

# Proposer le téléchargement CSV
csv = df_planning.to_csv(index=False).encode('utf-8')
st.download_button("📥 Télécharger en CSV", data=csv, file_name="planning.csv", mime='text/csv')

# Proposer le téléchargement Excel
excel_buffer = pd.ExcelWriter("planning_temp.xlsx", engine='xlsxwriter')
df_planning.to_excel(excel_buffer, index=False, sheet_name="Planning")
excel_buffer.close()
with open("planning_temp.xlsx", "rb") as f:
    st.download_button("📥 Télécharger en Excel", data=f, file_name="planning.xlsx", mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
