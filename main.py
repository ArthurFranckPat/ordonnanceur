#!/usr/bin/env python3
"""
Outil d'Ordonnancement OF - Point d'entrée principal
"""
import argparse
import logging
from datetime import datetime
from typing import Optional

from engine.config import Config
from engine.loader import DataLoader
from engine.calendar import BusinessCalendar
from engine.atp_calculator import ATPCalculator
from engine.matcher import OrderMOMatcher
from engine.allocator import Allocator
from engine.feasibility import FeasibilityChecker
from engine.exporter import Exporter


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure le logging"""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    return logging.getLogger(__name__)


def run_scheduler(
    data_dir: str,
    output_dir: str = "output",
    config: Optional[Config] = None,
    verbose: bool = False
) -> dict:
    """
    Lance le moteur d'ordonnancement
    
    Args:
        data_dir: Répertoire contenant les fichiers CSV
        output_dir: Répertoire de sortie pour les résultats
        config: Configuration (utilise défaut si None)
        verbose: Activer le logging détaillé
        
    Returns:
        Dictionnaire avec les résultats et statistiques
    """
    logger = setup_logging(verbose)
    
    # Utiliser config par défaut si non fournie
    if config is None:
        config = Config()
    
    start_time = datetime.now()
    logger.info(f"Démarrage de l'ordonnancement - {start_time}")
    logger.info(f"Répertoire données: {data_dir}")
    logger.info(f"Horizon: {config.horizon_weeks} semaines")
    
    # ===== ÉTAPE 1: Chargement des données =====
    logger.info("Étape 1/6: Chargement des données...")
    loader = DataLoader(data_dir, config)
    
    try:
        data = loader.load_all()
    except FileNotFoundError as e:
        logger.error(f"Erreur chargement: {e}")
        return {"success": False, "error": str(e)}
    
    # Valider les données
    errors = loader.validate_data(data)
    if errors:
        for error in errors:
            logger.warning(f"Validation: {error}")
    
    # Extraire les DataFrames
    orders_df = data["commandes_clients.csv"]
    stock_df = data["stock.csv"]
    receipts_df = data["receptions_attendues.csv"]
    mo_candidates_df = data["of_candidats.csv"]
    components_df = data["composants_of.csv"]
    items_df = data["referentiel_articles.csv"]
    closures_df = data.get("fermetures.csv", None)
    
    logger.info(f"  - Commandes: {len(orders_df)} lignes")
    logger.info(f"  - Stock: {len(stock_df)} articles")
    logger.info(f"  - Réceptions: {len(receipts_df)} lignes")
    logger.info(f"  - OF candidats: {len(mo_candidates_df)} OF")
    logger.info(f"  - Composants: {len(components_df)} lignes")
    
    # ===== ÉTAPE 2: Initialisation calendrier =====
    logger.info("Étape 2/6: Initialisation calendrier...")
    calendar = BusinessCalendar(
        closures=closures_df,
        country="FR"
    )
    
    # ===== ÉTAPE 3: Calcul ATP =====
    logger.info("Étape 3/6: Calcul ATP...")
    atp_calculator = ATPCalculator(calendar)
    atp_df = atp_calculator.calculate_atp(
        stock_df, receipts_df, config.reference_date
    )
    logger.info(f"  - ATP calculé pour {len(atp_df)} articles")
    
    # ===== ÉTAPE 4: Appariement commandes → OF =====
    logger.info("Étape 4/6: Appariement commandes → OF...")
    matcher = OrderMOMatcher(config, calendar)
    orders_enriched, match_results = matcher.enrich_orders(
        orders_df, mo_candidates_df
    )
    
    match_stats = orders_enriched["match_type"].value_counts().to_dict()
    logger.info(f"  - Types de match: {match_stats}")
    
    # ===== ÉTAPE 5: Allocation séquentielle =====
    logger.info("Étape 5/6: Allocation séquentielle...")
    allocator = Allocator(config, calendar, atp_calculator)
    allocation_df, allocation_results = allocator.allocate_orders(
        orders_enriched, atp_df, mo_candidates_df
    )
    
    alloc_stats = allocation_df["allocation_type"].value_counts().to_dict()
    logger.info(f"  - Types d'allocation: {alloc_stats}")
    
    # ===== ÉTAPE 6: Vérification faisabilité =====
    logger.info("Étape 6/6: Vérification faisabilité...")
    feasibility_checker = FeasibilityChecker(config, calendar, atp_calculator)
    feasibility_df, feasibility_results = feasibility_checker.check_feasibility(
        allocation_df, components_df, atp_df, items_df, receipts_df
    )
    
    # Composants manquants
    missing_df = feasibility_checker.get_missing_components_detail(feasibility_results)
    
    # ===== Génération des sorties =====
    logger.info("Génération des rapports...")
    exporter = Exporter(output_dir)
    output_path = exporter.export_all(
        orders_enriched=orders_enriched,
        feasibility_results=feasibility_results,
        allocation_results=allocation_df,
        feasibility_df=feasibility_df,
        missing_components_df=missing_df
    )
    
    # ===== Statistiques finales =====
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Calculer les KPIs
    total_orders = len(allocation_df)
    green_count = int((allocation_df["allocation_status"] == "green").sum()) if not allocation_df.empty else 0
    orange_count = int((allocation_df["allocation_status"] == "orange").sum()) if not allocation_df.empty else 0
    red_count = int((allocation_df["allocation_status"] == "red").sum()) if not allocation_df.empty else 0
    
    stats = {
        "success": True,
        "output_path": output_path,
        "duration_seconds": duration,
        "total_orders": total_orders,
        "green_count": green_count,
        "orange_count": orange_count,
        "red_count": red_count,
        "green_ratio": green_count / total_orders if total_orders > 0 else 0,
        "missing_components_count": len(missing_df),
        "match_stats": match_stats,
        "allocation_stats": alloc_stats
    }
    
    logger.info(f"=== RÉSUMÉ ===")
    logger.info(f"  Commandes analysées: {total_orders}")
    logger.info(f"  🟢 Vert: {green_count} ({stats['green_ratio']:.1%})")
    logger.info(f"  🟠 Orange: {orange_count}")
    logger.info(f"  🔴 Rouge: {red_count}")
    logger.info(f"  Composants manquants: {len(missing_df)}")
    logger.info(f"  Durée: {duration:.2f}s")
    logger.info(f"  Fichier sortie: {output_path}")
    
    return stats


def main():
    """Point d'entrée CLI"""
    parser = argparse.ArgumentParser(
        description="Outil d'ordonnancement OF - Aereco"
    )
    
    parser.add_argument(
        "data_dir",
        help="Répertoire contenant les fichiers CSV d'entrée"
    )
    
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Répertoire de sortie (défaut: output)"
    )
    
    parser.add_argument(
        "-w", "--horizon-weeks",
        type=int,
        default=8,
        help="Horizon en semaines (défaut: 8)"
    )
    
    parser.add_argument(
        "-d", "--production-offset",
        type=int,
        default=2,
        help="Décalage production en jours ouvrés (défaut: 2)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Activer le logging détaillé"
    )
    
    args = parser.parse_args()
    
    # Créer la config
    config = Config(
        horizon_weeks=args.horizon_weeks,
        production_offset_days=args.production_offset
    )
    
    # Lancer
    result = run_scheduler(
        data_dir=args.data_dir,
        output_dir=args.output,
        config=config,
        verbose=args.verbose
    )
    
    if result["success"]:
        print(f"\n✅ Ordonnancement terminé avec succès")
        print(f"   Fichier: {result['output_path']}")
        return 0
    else:
        print(f"\n❌ Erreur: {result.get('error', 'Erreur inconnue')}")
        return 1


if __name__ == "__main__":
    exit(main())
