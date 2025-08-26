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

def assign_roles_smart(start_date, weeks=52, seed=42):
    rnd = random.Random(seed)

    # --- Fenêtre de planification ---
    jours = [start_date + timedelta(days=i) for i in range(weeks * 7)]
    jours_ouvres = [d for d in jours if d.weekday() < 5]

    # --- Rôles en semaine & week-end ---
    ROLES_JOUR = ["Hospit1", "Hospit2", "HDL1", "HDL2", "HDM1", "HDM2"]
    ROLE_CONSULT = "Consult"
    ROLE_WE_SAM_HD = "HDL_Samedi"
    ROLE_WE_SAM_HO = "Hospit_Samedi"
    ROLE_WE_DIM_HO = "Hospit_Dimanche"

    # --- Données de base ---
    medecins = [m['nom'] for m in data['medecins']]
    planning = {}
    used_per_day = {}
    # "separes" : liste de 3 noms à éviter de placer le même jour sur HDL/HDM/Hospit
    separes = set(data.get("separes", []))

    # --- Indisponibilités jour par jour (vacances + dates interdites globales) ---
    indispo = {m['nom']: set() for m in data['medecins']}
    vac_spans = {m['nom']: [] for m in data['medecins']}  # pour le contrôle "week-end encadrant les vacances"
    for m in data['medecins']:
        # Vacances
        for v in m.get('vacances', []):
            d1 = datetime.strptime(v[0], "%Y-%m-%d").date()
            d2 = datetime.strptime(v[1], "%Y-%m-%d").date()
            vac_spans[m['nom']].append((d1, d2))
            for j in range((d2 - d1).days + 1):
                indispo[m['nom']].add(d1 + timedelta(days=j))
        # Dates interdites globales
        for di in data.get("dates_interdites_globales", []):
            indispo[m['nom']].add(datetime.strptime(di, "%Y-%m-%d").date())

    # (OPTIONNEL UI) Week-ends souhaités/interdits par médecin (si présents dans les données)
    weekends_interdits = {m['nom']: set(datetime.strptime(d, "%Y-%m-%d").date()
                                        for d in m.get("weekends_interdits", []))
                          for m in data['medecins']}
    weekends_souhaites = {m['nom']: set(datetime.strptime(d, "%Y-%m-%d").date()
                                        for d in m.get("weekends_souhaites", []))
                          for m in data['medecins']}

    # --- Helpers période A/B pour l'équilibrage des WE ---
    def periode_tag(d):
        y = d.year
        A_start = date(y, 5, 1)
        A_end   = date(y, 10, 31)
        B1_start= date(y, 11, 1)
        B1_end  = date(y+1, 4, 20)
        # samedi considéré
        if A_start <= d <= A_end:
            return ("A", y)
        if d >= B1_start:
            return ("B", y)
        if d <= date(y, 4, 20):
            return ("B", y-1)
        return ("A", y)  # fallback

    # --- Compteurs pour équilibrages ---
    count_role_year = {m: {"Hospit":0, "HDM":0, "HDL":0, "Consult":0} for m in medecins}
    count_we_period = {m: defaultdict(int) for m in medecins}  # clé: (tag, année_base)
    last_weekend = {m: None for m in medecins}  # dernier samedi travaillé (hdl ou hospit)

    # --- Fonctions contraintes ---
    def encadre_vacances(m, saturday):
        # Interdit de travailler le week-end qui touche directement une plage de vacances
        sunday = saturday + timedelta(days=1)
        for (d1, d2) in vac_spans[m]:
            if saturday == d1 - timedelta(days=1):  # veille de vacs
                return True
            if sunday == d2 + timedelta(days=1):    # lendemain de vacs
                return True
        return False

    def can_work_weekend(m, saturday):
        sunday = saturday + timedelta(days=1)
        # dispo les 2 jours ?
        if saturday in indispo[m] or sunday in indispo[m]:
            return False
        # pas 2 WE d'affilée + au moins 2 WE libres entre
        if last_weekend[m] is not None:
            delta = (saturday - last_weekend[m]).days
            if delta < 14:  # < 2 semaines
                return False
        # pas le WE encadrant les vacances
        if encadre_vacances(m, saturday):
            return False
        # préférences (si renseignées)
        if saturday in weekends_interdits.get(m, set()):
            return False
        return True

    def sep_conflict(noms_du_jour):
        # conflit si >=2 des "separes" sont dans les rôles HDL/HDM/Hospit le même jour
        s = separes.intersection(noms_du_jour)
        return len(s) >= 2

    # --- 1) Affectation des week-ends (équilibrage A/B) ---
    saturdays = [d for d in jours if d.weekday() == 5]
    # Cibles d'équilibre : on prend #WE dans la période / nb médecins
    # (approx : on vise une répartition homogène ; ajusté par la sélection dynamique)
    target_we = defaultdict(lambda: {m:0 for m in medecins})
    for sat in saturdays:
        tag = periode_tag(sat)
        target_we[tag]  # lazy creation
    for tag in target_we.keys():
        nb_we = sum(1 for sat in saturdays if periode_tag(sat) == tag)
        base = nb_we / max(len(medecins),1)
        for m in medecins:
            target_we[tag][m] = base

    for sat in saturdays:
        sun = sat + timedelta(days=1)
        jour_s = str(sat)
        jour_d = str(sun)
        planning.setdefault(jour_s, {})
        planning.setdefault(jour_d, {})
        used_per_day.setdefault(jour_s, set())
        used_per_day.setdefault(jour_d, set())

        # candidats valides
        cand = [m for m in medecins if can_work_weekend(m, sat)
                and (m not in used_per_day[jour_s]) and (m not in used_per_day[jour_d])]
        # score d'écart à la cible période
        tag = periode_tag(sat)
        def we_score(m):
            # écart au target dans la période + bonus si "souhaité"
            dev = abs(count_we_period[m][tag] + 1 - target_we[tag][m])
            bonus = -0.2 if sat in weekends_souhaites.get(m, set()) else 0.0
            return dev + bonus + rnd.random()*0.01

        cand.sort(key=we_score)
        if len(cand) >= 2:
            m_hdl = cand[0]
            m_hosp = cand[1]
        elif len(cand) == 1:
            # on préfère au moins placer l'hospit (plus prioritaire)
            m_hdl = cand[0]
            # second choix : autoriser quelqu’un à travailler même si pas "souhaité" mais sans casser les règles dures
            restant = [x for x in medecins if x != m_hdl and can_work_weekend(x, sat)]
            if not restant:
                continue
            restant.sort(key=we_score)
            m_hosp = restant[0]
        else:
            continue

        # place
        planning[jour_s][ROLE_WE_SAM_HD] = m_hdl
        planning[jour_s][ROLE_WE_SAM_HO] = m_hosp
        planning[jour_d][ROLE_WE_DIM_HO] = m_hosp
        used_per_day[jour_s].update([m_hdl, m_hosp])
        used_per_day[jour_d].add(m_hosp)
        last_weekend[m_hdl] = sat
        last_weekend[m_hosp] = sat
        count_we_period[m_hdl][tag] += 1
        count_we_period[m_hosp][tag] += 1

    # --- 2) Blocks en semaine pour Hospit, puis HDM (priorité à Hospit) ---
    def bloc_iter(jours_base, bloc_semaines, bloc_semaines_alt=None):
        # génère des blocs de k semaines (ouvrées), k = bloc_semaines (ou alt si fourni et nécessaire)
        idx = 0
        while idx < len(jours_base):
            # prend un bloc d'environ k semaines ouvrées
            k = bloc_semaines
            bloc = []
            dcount = 0
            j = idx
            while j < len(jours_base) and dcount < 5*bloc_semaines:
                d = jours_base[j]
                bloc.append(d)
                dcount += 1
                j += 1
            if bloc_semaines_alt and len(bloc) < 5*bloc_semaines and (j+5 <= len(jours_base)):
                # on “étire” à l’alternative si possible (2→3 semaines pour Hospit, ou 2→3/1 pour HDM)
                extra = min(5*(bloc_semaines_alt-bloc_semaines), len(jours_base)-j)
                bloc.extend(jours_base[j:j+extra])
                j += extra
            yield bloc
            idx = j

    def choose_for_role(role, bloc, avoid_pairs, prio_key):
        # prio_key: "Hospit" | "HDM" | "HDL"
        def admissible(m):
            # disponible tous les jours du bloc + pas déjà pris ce jour + respecte séparation
            for d in bloc:
                js = str(d)
                if d in indispo[m]: return False
                if m in used_per_day.get(js, set()): return False
            # séparation (eviter 2 des 'separes' le même jour sur HDL/HDM/Hospit)
            for d in bloc:
                js = str(d)
                noms_du_jour = set(used_per_day.get(js, set()))
                # on regarde uniquement les rôles "HDL/HDM/Hospit" déjà posés
                deja = []
                for r in ["Hospit1","Hospit2","HDL1","HDL2","HDM1","HDM2"]:
                    n = planning.get(js, {}).get(r)
                    if isinstance(n, str):
                        deja.append(n)
                if sep_conflict(set(deja + ([m] if m in separes else []))):
                    return False
            return True

        # score équilibration + petit aléa
        def sc(m):
            return (count_role_year[m][prio_key]) + rnd.random()*0.01

        candidats = [m for m in medecins if admissible(m) and m not in avoid_pairs]
        if not candidats:
            return None
        candidats.sort(key=sc)
        return candidats[0]

    # Hospit: blocs 2–3 semaines
    for role in ["Hospit1","Hospit2"]:
        for bloc in bloc_iter(jours_ouvres, bloc_semaines=2, bloc_semaines_alt=3):
            avoid = set()  # pas besoin de pair spécifique ici
            m = choose_for_role(role, bloc, avoid, prio_key="Hospit")
            if m is None:
                continue
            for d in bloc:
                js = str(d)
                planning.setdefault(js, {})[role] = m
                used_per_day.setdefault(js, set()).add(m)
                count_role_year[m]["Hospit"] += 1

    # HDM: blocs 2 semaines (3 ou 1 si obligé)
    for role in ["HDM1","HDM2"]:
        for bloc in bloc_iter(jours_ouvres, bloc_semaines=2, bloc_semaines_alt=3):
            m = choose_for_role(role, bloc, avoid_pairs=set(), prio_key="HDM")
            if m is None:
                # tenter bloc plus court (1 semaine) si tout bloque
                bloc_short = bloc[:5] if len(bloc) >= 5 else bloc
                m = choose_for_role(role, bloc_short, avoid_pairs=set(), prio_key="HDM")
                if m is None:
                    continue
                bloc_to_use = bloc_short
            else:
                bloc_to_use = bloc
            for d in bloc_to_use:
                js = str(d)
                planning.setdefault(js, {})[role] = m
                used_per_day.setdefault(js, set()).add(m)
                count_role_year[m]["HDM"] += 1

    # --- 3) HDL au jour le jour + règles 5/4 présents ---
    for d in jours_ouvres:
        js = str(d)
        planning.setdefault(js, {})
        used_per_day.setdefault(js, set())

        presents = [m for m in medecins if d not in indispo[m]]
        # Exclure ceux déjà pris sur ce jour ailleurs
        libres = [m for m in presents if m not in used_per_day[js]]

        # Règle effectifs: si 5 présents → un médecin couvre HDL1 & HDM1 ; si 4 → HDL2 & HDM2
        if len(presents) == 5 and "HDM1" in planning[js]:
            m1 = planning[js]["HDM1"]
            if m1 not in used_per_day[js]:
                planning[js]["HDL1"] = m1
                used_per_day[js].add(m1)
                count_role_year[m1]["HDL"] += 1

        if len(presents) == 4 and "HDM2" in planning[js]:
            m2 = planning[js]["HDM2"]
            if m2 not in used_per_day[js]:
                planning[js]["HDL2"] = m2
                used_per_day[js].add(m2)
                count_role_year[m2]["HDL"] += 1

        # compléter HDL1/HDL2 manquants en respectant séparation
        for role in ["HDL1","HDL2"]:
            if role in planning[js]:
                continue
            candidats = []
            for m in libres:
                # check séparation avec les rôles déjà posés (Hospit*, HDM*, HDL*)
                deja = []
                for r in ["Hospit1","Hospit2","HDL1","HDL2","HDM1","HDM2"]:
                    n = planning[js].get(r)
                    if isinstance(n, str): deja.append(n)
                if sep_conflict(set(deja + ([m] if m in separes else []))):
                    continue
                candidats.append(m)
            if not candidats:
                continue
            # équilibrage HDL
            candidats.sort(key=lambda x: (count_role_year[x]["HDL"], rnd.random()))
            choisi = candidats[0]
            planning[js][role] = choisi
            used_per_day[js].add(choisi)
            count_role_year[choisi]["HDL"] += 1

        # Surplus => Consultation (tous les libres restants)
        restants = [m for m in presents if m not in used_per_day[js]]
        if restants:
            planning[js].setdefault(ROLE_CONSULT, [])
            for m in restants:
                planning[js][ROLE_CONSULT].append(m)
                count_role_year[m]["Consult"] += 1
                used_per_day[js].add(m)

    # --- Finalisation ---
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

