import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_weighted_bootstrap(
    df: pd.DataFrame,
    group_col: str,
    n_iterations: int = 1000,
    nb_years: float = 1.0,
    batch_size: int = 1000,
    seed: int | None = 42,
) -> list[dict]:
    """
    Calcule le taux de casse annuel moyen et son intervalle de confiance à 95 %
    (bootstrap par rééchantillonnage des tronçons, traité par lots pour limiter la RAM).

    Seuls les tronçons de longueur finie et strictement positive sont pris en compte :
    un tronçon sans géométrie (longueur NaN) rendait le taux moyen NaN (puis 0 après
    fillna) tout en laissant le bootstrap calculer un IC sur les rares échantillons
    sans NaN — d'où des IC qui ne contenaient pas le taux.

    Complexité : O(G · n_iterations · n) en temps, O(batch_size · n) en mémoire par groupe.
    """
    rng = np.random.default_rng(seed)
    results = []

    km_all = pd.to_numeric(df["longueur_km"], errors="coerce").to_numpy(dtype=float)
    casses_all = pd.to_numeric(df["nb_casses"], errors="coerce").to_numpy(dtype=float)
    valid_rows = np.isfinite(km_all) & (km_all > 0) & np.isfinite(casses_all)

    n_invalid = int((~valid_rows).sum())
    if n_invalid:
        logger.warning(
            f"[bootstrap:{group_col}] {n_invalid} tronçon(s) sans longueur exploitable exclus du calcul."
        )

    groups = df[group_col].to_numpy()

    for group in pd.unique(groups):
        if pd.isna(group):  # or group == "Indéterminé":
            continue

        mask = (groups == group) & valid_rows
        km_arr = km_all[mask]
        casses_arr = casses_all[mask]
        n_rows = km_arr.size

        if n_rows < 2:
            continue

        mean_rate = (casses_arr.sum() / km_arr.sum()) / nb_years

        boot_rates = np.empty(n_iterations, dtype=float)
        done = 0
        while done < n_iterations:
            current = min(batch_size, n_iterations - done)
            idx = rng.integers(0, n_rows, size=(current, n_rows))
            # km > 0 sur chaque ligne valide → la somme d'un échantillon est toujours > 0
            boot_rates[done : done + current] = casses_arr[idx].sum(axis=1) / km_arr[
                idx
            ].sum(axis=1)
            done += current
        boot_rates /= nb_years

        results.append(
            {
                "analyse_type": group_col,
                "categorie": str(group),
                "taux_moyen": float(mean_rate),
                "ic_inf": float(np.percentile(boot_rates, 2.5)),
                "ic_sup": float(np.percentile(boot_rates, 97.5)),
                "nb_entites": int(n_rows),
            }
        )

    return results
