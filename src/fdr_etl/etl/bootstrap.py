import numpy as np
import pandas as pd


def compute_weighted_bootstrap(
    df, group_col, n_iterations=1000, nb_years=1.0, batch_size=1000
):
    """
    Calcule le taux de casse annuel moyen et l'intervalle de confiance.
    Version hautement optimisée avec traitement par lots (mini-batches)
    pour éviter les crashs de mémoire (SIGKILL / OOM) à 10 000 scénarios.
    """
    results = []
    groups = df[group_col].unique()

    for group in groups:
        if pd.isna(group) or group == "Indéterminé":
            continue

        group_data = df[df[group_col] == group]
        n_rows = len(group_data)

        if n_rows < 2 or group_data["longueur_km"].sum() <= 0:
            continue

        # Passage sur des tableaux NumPy bruts
        km_arr = group_data["longueur_km"].to_numpy()
        casses_arr = group_data["nb_casses"].to_numpy()

        boot_km_list = []
        boot_casses_list = []

        # 🔄 DÉCOUPAGE EN LOTS POUR PROTÉGER LA RAM
        # Si n_iterations = 10000 et batch_size = 1000, on fait 10 boucles ultra-légères
        iterations_faites = 0
        while iterations_faites < n_iterations:
            current_batch = min(batch_size, n_iterations - iterations_faites)

            # Grille temporaire de taille réduite (ex: 1000 x N au lieu de 10000 x N)
            indices = np.random.choice(
                n_rows, size=(current_batch, n_rows), replace=True
            )

            boot_km_list.append(km_arr[indices].sum(axis=1))
            boot_casses_list.append(casses_arr[indices].sum(axis=1))

            iterations_faites += current_batch

        # Fusion des lots en un seul vecteur final de taille (10000,) -> Léger en RAM
        boot_km = np.concatenate(boot_km_list)
        boot_col = np.concatenate(boot_casses_list)

        valid_mask = boot_km > 0
        if np.any(valid_mask):
            boot_rates = (boot_col[valid_mask] / boot_km[valid_mask]) / nb_years
            mean_rate = (casses_arr.sum() / km_arr.sum()) / nb_years

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
