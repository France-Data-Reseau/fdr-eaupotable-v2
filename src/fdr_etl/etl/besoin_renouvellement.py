# -*- coding: utf-8 -*-
"""
Module de calcul du besoin de renouvellement des canalisations.
Projection du besoin théorique (Weibull) + rattrapage du backlog lissé.
Stocke toutes les métriques nécessaires pour Superset.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
import psycopg
from sqlalchemy import create_engine, text

from fdr_etl.etl.material_config import (
    DICT_MAT_FAMILY,
    DEFAULT_ESL,
    DEFAULT_SHAPE,
)

logger = logging.getLogger(__name__)


def weibull_cumulative(t, shape: float, scale: float):
    return np.where(t <= 0, 0.0, 1 - np.exp(-(t / scale) ** shape))


def get_historic_start_year(engine, import_id_str: str) -> int:
    """Année de début de l'historique fiable des réparations."""
    query = text("""
        SELECT MIN(EXTRACT(YEAR FROM r."dateIntervention"::timestamp)) as min_year
        FROM aep_reparation r
        INNER JOIN aep_canalisation c ON r.supportincident_auto = c.id_aep_canalisation
        WHERE c.file_id = :import_id AND r."dateIntervention" IS NOT NULL
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"import_id": import_id_str}).scalar()
    if result and result > 1900:
        logger.info(f"📅 Année de début d'observation : {int(result)}")
        return int(result)
    logger.warning("⚠️ Début observation inconnu → 2000")
    return 2000


def get_pipe_data_for_renewal(engine, import_id_str: str) -> pd.DataFrame:
    """Récupère les données canalisations pour un import."""
    query = text("""
        SELECT
            c.id_aep_canalisation,
            c.materiau,
            c.date_pose,
            EXTRACT(YEAR FROM CURRENT_DATE) - c.date_pose as age,
            COALESCE(r.cnt, 0) as nb_casses,
            r.annee_1ere_casse - c.date_pose as age_premiere_casse,
            ST_Length(c.geom) / 1000.0 as longueur_km,
            c.diametre_num,
            COALESCE(c.dia_ens, 'Indéterminé') as dia_ens,
            COALESCE(c.ddp_ens, 'Indéterminé') as ddp_ens,
            c.file_id
        FROM aep_canalisation c
        LEFT JOIN (
            SELECT
                r.supportincident_auto,
                COUNT(*) as cnt,
                MIN(EXTRACT(YEAR FROM r."dateIntervention"::timestamp)) as annee_1ere_casse
            FROM aep_reparation r
            INNER JOIN aep_canalisation rc ON r.supportincident_auto = rc.id_aep_canalisation
            WHERE r."dateIntervention" IS NOT NULL
              AND EXTRACT(YEAR FROM r."dateIntervention"::timestamp) >= rc.date_pose
            GROUP BY r.supportincident_auto
        ) r ON r.supportincident_auto = c.id_aep_canalisation
        WHERE c.type_reseau = 'aep'
          AND (c.fictif IS NULL OR LOWER(TRIM(c.fictif)) NOT IN ('1', 'true', 'vrai'))
          AND c.etat_service = 'en_service'
          AND (c.diametre_num IS NULL OR c.diametre_num > 25)
          AND c.date_pose IS NOT NULL AND c.date_pose > 1700
          AND c.date_pose <= EXTRACT(YEAR FROM CURRENT_DATE)
          AND c.file_id = :import_id
    """)
    return pd.read_sql(query, engine, params={"import_id": import_id_str})


def calculate_base_projections(df_pipes, esl_dict, shape_dict, horizon_years=120):
    """
    Calcule les projections de besoin théorique (sans rattrapage).
    Retourne (df_annual, df_details, backlog_km, backlog_euro)
    """
    current_year = datetime.now().year
    raw_max_esl = max(esl_dict.values(), default=100)
    max_esl = min(raw_max_esl, 200)
    start_year = max(current_year - int(max_esl * 1.5), 1900)
    years = np.arange(start_year, current_year + horizon_years + 1)

    agg_records = []
    detail_records = []

    for materiau, df_mat in df_pipes.groupby('materiau'):
        shape = shape_dict.get(materiau, 2.0)
        scale = esl_dict.get(materiau, 70)
        if scale > 1000 or scale < 1:
            logger.warning(f"Scale invalide {materiau}: {scale} -> 70")
            scale, shape = 70.0, 2.0

        inst_years = df_mat['date_pose'].values
        lengths_km = df_mat['longueur_km'].values
        diameters = df_mat['diametre_num'].fillna(100).values
        file_ids = df_mat['file_id'].values

        t_matrix = years[np.newaxis, :] - inst_years[:, np.newaxis]
        t_matrix = np.clip(t_matrix, 0, None)
        t_prev = np.clip(t_matrix - 1, 0, None)
        prob_matrix = weibull_cumulative(t_matrix, shape, scale) - weibull_cumulative(t_prev, shape, scale)

        cost_per_m = 0.0004 * diameters**2 + 0.4579 * diameters + 248.6
        inflation = (1.025) ** np.maximum(0, years - current_year)

        renewal_m = lengths_km[:, np.newaxis] * prob_matrix * 1000
        cost_mat = renewal_m * cost_per_m[:, np.newaxis] * inflation[np.newaxis, :]

        agg_records.append(pd.DataFrame({
            'annee': years,
            'categorie_materiau': DICT_MAT_FAMILY.get(materiau, 'Indéterminé'),
            'besoin_m': renewal_m.sum(axis=0),
            'cout_euro': cost_mat.sum(axis=0),
        }))

        pipe_idx, year_idx = np.where(renewal_m > 0.01)
        if len(pipe_idx):
            detail_records.append(pd.DataFrame({
                'annee': years[year_idx],
                'materiau': materiau,
                'categorie_materiau': DICT_MAT_FAMILY.get(materiau, 'Indéterminé'),
                'dia_ens': df_mat['dia_ens'].values[pipe_idx],
                'ddp_ens': df_mat['ddp_ens'].values[pipe_idx],
                'besoin_m': renewal_m[pipe_idx, year_idx],
                'cout_euro': cost_mat[pipe_idx, year_idx],
                'file_id': file_ids[pipe_idx],
            }))

    df_annual = (pd.concat(agg_records)
                 .groupby(['annee', 'categorie_materiau'])
                 .sum()
                 .reset_index()
                 .rename(columns={'besoin_m': 'besoin_km', 'cout_euro': 'cout_renouvellement_euro'}))
    df_annual['besoin_km'] = df_annual['besoin_km'] / 1000

    df_details = pd.concat(detail_records).reset_index(drop=True) if detail_records else pd.DataFrame()

    # Backlog (années passées)
    current_year = datetime.now().year
    df_backlog = df_details[df_details['annee'] < current_year]
    backlog_km = df_backlog['besoin_m'].sum() / 1000 if not df_backlog.empty else 0
    backlog_euro = df_backlog['cout_euro'].sum() if not df_backlog.empty else 0

    return df_annual, df_details, backlog_km, backlog_euro


def apply_catchup(df_annual_global, backlog_km, catchup_years, total_length_km):
    """
    Applique le rattrapage sur un DataFrame agrégé par année (une ligne par année).
    Retourne le taux annuel moyen sur la période (en % du linéaire total).
    """
    current_year = datetime.now().year
    mask = (df_annual_global['annee'] > current_year) & (df_annual_global['annee'] <= current_year + catchup_years)
    n_years = mask.sum()
    if n_years == 0 or backlog_km <= 0:
        return 0.0
    annual_catchup_km = backlog_km / n_years
    # On ajoute le rattrapage à chaque année de la période
    df_temp = df_annual_global.copy()
    df_temp.loc[mask, 'besoin_km'] += annual_catchup_km
    mean_annual = df_temp.loc[mask, 'besoin_km'].mean()
    taux = (mean_annual / total_length_km) * 100 if total_length_km > 0 else 0
    return taux


def init_renewal_tables(pg_cursor):
    """Initialise les tables avec toutes les colonnes nécessaires pour Superset."""
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS besoin_renouvellement (
            id SERIAL PRIMARY KEY,
            annee INTEGER NOT NULL,
            categorie_materiau TEXT,
            besoin_renouvellement_km FLOAT,
            cout_renouvellement_euro FLOAT,
            scope TEXT NOT NULL,
            file_id UUID,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    pg_cursor.execute("""
        CREATE TABLE IF NOT EXISTS indicateurs_renouvellement (
            id SERIAL PRIMARY KEY,
            file_id UUID,
            scope TEXT NOT NULL,
            longueur_totale_km FLOAT,
            backlog_total_km FLOAT,
            backlog_total_euro FLOAT,
            backlog_pct FLOAT,
            taux_sans_rattrapage_5ans_pct FLOAT,
            taux_sans_rattrapage_10ans_pct FLOAT,
            taux_sans_rattrapage_20ans_pct FLOAT,
            taux_sans_rattrapage_30ans_pct FLOAT,
            taux_avec_rattrapage_5ans_pct FLOAT,
            taux_avec_rattrapage_10ans_pct FLOAT,
            taux_avec_rattrapage_20ans_pct FLOAT,
            taux_avec_rattrapage_30ans_pct FLOAT,
            cout_moyen_km_euro FLOAT,
            horizon_ans INTEGER,
            date_calcul TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_be_ren_annee ON besoin_renouvellement(annee);")
    pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_be_ren_file_id ON besoin_renouvellement(file_id);")
    pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_be_ren_scope ON besoin_renouvellement(scope);")


def run_renewal_pipeline(
    db_url: str,
    import_id: str,
    horizon_years: int = 50,
) -> dict:
    """
    Pipeline principal pour un import spécifique.
    Calcule et stocke toutes les métriques nécessaires à Superset.
    """
    import_id_str = str(import_id)
    scope = 'individual'
    logger.info(f"🚀 Démarrage pipeline - import_id: {import_id_str}")

    engine_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1) if db_url.startswith("postgresql://") else db_url
    engine = create_engine(engine_url)

    try:
        logger.info("📊 Chargement des canalisations...")
        df_pipes = get_pipe_data_for_renewal(engine, import_id_str)
        if df_pipes.empty:
            logger.warning(f"Aucune donnée pour {import_id_str}")
            return {}

        total_length_km = df_pipes['longueur_km'].sum()
        logger.info(f"✅ {len(df_pipes)} canalisations, {total_length_km:.1f} km")

        # Paramètres par défaut (pas de calibration)
        esl_dict = DEFAULT_ESL.copy()
        shape_dict = DEFAULT_SHAPE.copy()
        for mat in df_pipes['materiau'].unique():
            if pd.notna(mat) and mat not in esl_dict:
                esl_dict[mat] = 70
                shape_dict[mat] = 2.0
        logger.info("📋 Utilisation des valeurs par défaut Weibull.")

        # Afficher les paramètres utilisés pour diagnostic
        logger.info("🔧 Paramètres Weibull appliqués :")
        for mat in df_pipes['materiau'].unique():
            if pd.isna(mat):
                continue
            logger.info(f"   {mat:<15} -> shape={shape_dict.get(mat,2.0):.2f}, scale={esl_dict.get(mat,70):.0f} ans")

        # Projection de base (détail par catégorie)
        df_base, df_details, backlog_km, backlog_euro = calculate_base_projections(
            df_pipes, esl_dict, shape_dict, horizon_years
        )
        if df_base.empty:
            logger.warning("Aucune projection")
            return {}

        current_year = datetime.now().year

        # --- Taux sans rattrapage : agréger d'abord par année (toutes catégories) ---
        df_annual_global = df_base.groupby('annee', as_index=False)['besoin_km'].sum()

        def average_annual_rate(period_years):
            mask = (df_annual_global['annee'] > current_year) & (df_annual_global['annee'] <= current_year + period_years)
            if mask.any():
                avg_km = df_annual_global.loc[mask, 'besoin_km'].mean()
                return (avg_km / total_length_km * 100) if total_length_km > 0 else 0
            return 0.0

        taux_sans_5  = average_annual_rate(5)
        taux_sans_10 = average_annual_rate(10)
        taux_sans_20 = average_annual_rate(20)
        taux_sans_30 = average_annual_rate(30)

        # --- Taux avec rattrapage (sur données agrégées annuellement) ---
        catchup_horizons = [5, 10, 20, 30]
        taux_avec = {}
        for years in catchup_horizons:
            taux_avec[years] = apply_catchup(df_annual_global, backlog_km, years, total_length_km)

        # Coût moyen au km (sur le besoin futur total)
        total_cost = df_base['cout_renouvellement_euro'].sum()
        total_km_needed = df_base['besoin_km'].sum()
        avg_cost_per_km = total_cost / total_km_needed if total_km_needed > 0 else 0

        # Backlog en pourcentage
        backlog_pct = (backlog_km / total_length_km * 100) if total_length_km > 0 else 0

        # Stockage en base
        logger.info("💾 Sauvegarde des résultats...")
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                init_renewal_tables(cur)
                cur.execute("DELETE FROM besoin_renouvellement WHERE file_id = %s AND scope = %s", (import_id_str, scope.upper()))
                cur.execute("DELETE FROM indicateurs_renouvellement WHERE file_id = %s AND scope = %s", (import_id_str, scope.upper()))

                # Insertion des projections détaillées (besoin théorique pur)
                records = [
                    (int(row.annee), row.categorie_materiau, float(row.besoin_km), float(row.cout_renouvellement_euro), scope.upper(), import_id_str)
                    for row in df_base.itertuples()
                ]
                cur.executemany("""
                    INSERT INTO besoin_renouvellement
                    (annee, categorie_materiau, besoin_renouvellement_km, cout_renouvellement_euro, scope, file_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, records)

                # Insertion des indicateurs agrégés
                cur.execute("""
                    INSERT INTO indicateurs_renouvellement
                    (file_id, scope,
                     longueur_totale_km,
                     backlog_total_km, backlog_total_euro, backlog_pct,
                     taux_sans_rattrapage_5ans_pct,
                     taux_sans_rattrapage_10ans_pct,
                     taux_sans_rattrapage_20ans_pct,
                     taux_sans_rattrapage_30ans_pct,
                     taux_avec_rattrapage_5ans_pct,
                     taux_avec_rattrapage_10ans_pct,
                     taux_avec_rattrapage_20ans_pct,
                     taux_avec_rattrapage_30ans_pct,
                     cout_moyen_km_euro,
                     horizon_ans)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    import_id_str, scope.upper(),
                    round(total_length_km, 1),
                    round(backlog_km, 1), round(backlog_euro, 0), round(backlog_pct, 1),
                    round(taux_sans_5, 1),
                    round(taux_sans_10, 1),
                    round(taux_sans_20, 1),
                    round(taux_sans_30, 1),
                    round(taux_avec[5], 1),
                    round(taux_avec[10], 1),
                    round(taux_avec[20], 1),
                    round(taux_avec[30], 1),
                    round(avg_cost_per_km, 0),
                    horizon_years
                ))
                conn.commit()

        logger.info(f"""
        ==========================================================
        📊 RÉSULTATS - {scope.upper()}
        ==========================================================
        📏 Linéaire total : {total_length_km:.1f} km
        🔴 Backlog : {backlog_km:.1f} km ({backlog_pct:.1f}% du réseau)

        📈 Taux sans rattrapage (moyenne annuelle) :
            - 5 ans  : {taux_sans_5:.1f}%/an
            - 10 ans : {taux_sans_10:.1f}%/an
            - 20 ans : {taux_sans_20:.1f}%/an
            - 30 ans : {taux_sans_30:.1f}%/an

        🚀 Taux avec rattrapage du backlog lissé :
            - 5 ans  : {taux_avec[5]:.1f}%/an
            - 10 ans : {taux_avec[10]:.1f}%/an
            - 20 ans : {taux_avec[20]:.1f}%/an
            - 30 ans : {taux_avec[30]:.1f}%/an

        💶 Coût moyen du renouvellement : {avg_cost_per_km:.0f} €/km
        ==========================================================
        """)

        return {
            'longueur_totale_km': total_length_km,
            'backlog_km': backlog_km,
            'backlog_pct': backlog_pct,
            'taux_sans_rattrapage': {'5': taux_sans_5, '10': taux_sans_10, '20': taux_sans_20, '30': taux_sans_30},
            'taux_avec_rattrapage': taux_avec,
            'cout_moyen_km': avg_cost_per_km,
        }

    except Exception as e:
        logger.error(f"❌ Erreur: {e}", exc_info=True)
        raise
    finally:
        engine.dispose()