import pandas as pd
from datetime import datetime
import numpy as np
import logging
import time
from fdr_etl.etl.bootstrap import compute_weighted_bootstrap

logger = logging.getLogger(__name__)

def execute_statistical_pipeline(df_source: pd.DataFrame, nom_coll: str, nb_annees: float):
    if df_source.empty:
        logger.info(f"[{nom_coll}] DataFrame source vide. Fin prématurée du pipeline.")
        return pd.DataFrame(), pd.DataFrame(), {}

    pipeline_start = time.time()  # Top départ global
    logger.info(f"[{nom_coll}] Démarrage du pipeline statistique (Taille : {len(df_source)} lignes)")

    # Calculs préliminaires vectorisés
    start_step = time.time()
    total_km = df_source['longueur_km'].fillna(0).sum()
    current_yr = datetime.now().year
    
    # Nettoyage et calcul âge
    df_source['annee_pose_brute'] = pd.to_numeric(df_source['date_pose'], errors='coerce')
    df_source['age'] = current_yr - df_source['annee_pose_brute']
    
    # Imputation robuste : Médiane locale, sinon valeur de secours nationale (40 ans) si le fichier est vide
    age_median = df_source['age'].median()
    if pd.isna(age_median):
        age_median = 40.0
    df_source['age_impute'] = df_source['age'].fillna(age_median)
    
    # Âge moyen pondéré
    age_moyen = (df_source['age_impute'] * df_source['longueur_km']).sum() / total_km if total_km > 0 else 0.0
    
    # Taux globaux
    total_casses = df_source['nb_casses'].sum()
    taux_global_an = (total_casses / total_km) / nb_annees if total_km > 0 and nb_annees > 0 else 0.0
    
    # Taux de renouvellement sur 5 ans
    conduites_recentes = df_source['annee_pose_brute'] >= (current_yr - 5)
    lin_renouvele = df_source.loc[conduites_recentes, 'longueur_km'].sum()
    taux_renouv_5ans = ((lin_renouvele / 5) / total_km) * 100 if total_km > 0 else 0.0
    logger.info(f"[{nom_coll}] Étape 1 (Calculs préliminaires) terminée en {time.time() - start_step:.3f}s")

    # Matériau dominant
    start_step = time.time()
    rep_mat = df_source.groupby('materiau', observed=False)['longueur_km'].sum()
    if not rep_mat.empty and total_km > 0:
        mat_dom = rep_mat.idxmax()
        pct_dom = (rep_mat.max() / total_km) * 100
        txt_mat_dominant = f"{mat_dom} ({pct_dom:.1f}%)"
    else:
        txt_mat_dominant = "Indéterminé"
    logger.info(f"[{nom_coll}] Étape 2 (Matériau dominant) terminée en {time.time() - start_step:.3f}s")

    # BOOTSTRAP + PATRIMOINE combinés
    start_bootstrap_global = time.time()
    mapping_types = {'materiau': 'rep_materiau', 'dia_ens': 'rep_dia_ens', 'ddp_ens': 'rep_ddp_ens'}
    all_stats = []
    df_list = []
    
    for col, key in mapping_types.items():
        start_col_bootstrap = time.time()  # Chrono pour CHAQUE colonne
        
        # Agrégation du linéaire local pour cette ventilation
        temp = df_source.groupby(col, observed=False)['longueur_km'].sum().reset_index().rename(columns={col: 'categorie', 'longueur_km': 'km'})
        temp['analyse_type'] = key
        df_list.append(temp)
        
        # Lancement du Bootstrap
        logger.debug(f"[{nom_coll}] Lancement du bootstrap pour la colonne : {col}...")
        stats = compute_weighted_bootstrap(df_source, col, nb_years=nb_annees)
        
        for s in stats:
            s['analyse_type'] = key
            all_stats.append(s)
            
        logger.info(f"[{nom_coll}] Bootstrap pour '{col}' terminé en {time.time() - start_col_bootstrap:.3f}s")
    
    logger.info(f"[{nom_coll}] Étape 3 (Total des 3 Bootstraps) terminée en {time.time() - start_bootstrap_global:.3f}s")
    
    # Finalisation et assemblages
    start_step = time.time()
    df_patrimoine = pd.concat(df_list, ignore_index=True)
    df_stats = pd.DataFrame(all_stats)
    
    # Assignation du nom de la collectivité (seule métadonnée textuelle utile à l'analyse contextuelle)
    df_patrimoine['nom_collectivite'] = nom_coll

    # Fusion finale avec les résultats statistiques du bootstrap
    if not df_stats.empty:
        df_stats = df_stats.drop_duplicates(subset=['analyse_type', 'categorie'])
        df_stats['categorie'] = df_stats['categorie'].astype(str)
        df_patrimoine['categorie'] = df_patrimoine['categorie'].astype(str)
        df_patrimoine = df_patrimoine.merge(df_stats, on=['analyse_type', 'categorie'], how='left')
    else:
        df_patrimoine['taux_moyen'] = 0.0
        df_patrimoine['ic_inf'] = 0.0
        df_patrimoine['ic_sup'] = 0.0

    # Nettoyage final des valeurs invalides / NaN
    df_patrimoine[['ic_inf', 'ic_sup', 'taux_moyen']] = df_patrimoine[['ic_inf', 'ic_sup', 'taux_moyen']].fillna(0.0)
    df_patrimoine.replace([np.inf, -np.inf], 0.0, inplace=True)
    
    # Sortie croisée pour la consolidation nationale (Etape 5)
    df_croise = df_source.groupby(['materiau', 'dia_ens', 'ddp_ens'], observed=False).agg({'longueur_km': 'sum', 'nb_casses': 'sum'}).reset_index()
    
    # Construction de l'objet de métadonnées globales de l'import
    metadata = {
        "lineaire_total": float(total_km),
        "periode_obs": float(nb_annees),
        "age_moyen": float(age_moyen),
        "taux_global": float(taux_global_an),
        "taux_renouv": float(taux_renouv_5ans),
        "mat_dominant": txt_mat_dominant
    }

    logger.info(f"[{nom_coll}] Étape 4 & 5 (Assemblage et croisement) terminés en {time.time() - start_step:.3f}s")
    logger.info(f"[{nom_coll}] Pipeline TOTAL exécuté en {time.time() - pipeline_start:.3f}s")
    
    return df_patrimoine, df_croise, metadata