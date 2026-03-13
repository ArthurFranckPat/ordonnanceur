# Exportateur des résultats
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# Pour Excel avec formatage
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils.dataframe import dataframe_to_rows
    from openpyxl.formatting.rule import FormulaRule
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

from .feasibility import FeasibilityResult


class Exporter:
    """Exporte les résultats en Excel/CSV"""
    
    # Couleurs pour les statuts
    COLORS = {
        "green": "90EE90",    # Light green
        "orange": "FFD700",   # Gold
        "red": "FF6B6B",      # Light red
        "header": "4472C4",   # Blue
        "header_font": "FFFFFF"
    }
    
    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def export_all(
        self,
        orders_enriched: pd.DataFrame,
        feasibility_results: List[FeasibilityResult],
        allocation_results: pd.DataFrame,
        feasibility_df: pd.DataFrame,
        missing_components_df: pd.DataFrame,
        filename: Optional[str] = None
    ) -> str:
        """
        Exporte tous les résultats dans un fichier Excel multi-onglets
        
        Returns:
            Chemin du fichier créé
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"ordonnancement_{timestamp}.xlsx"
        
        filepath = self.output_dir / filename
        
        if EXCEL_AVAILABLE:
            self._export_excel_formatted(
                filepath,
                orders_enriched,
                feasibility_results,
                allocation_results,
                feasibility_df,
                missing_components_df,
            )
        else:
            # Fallback CSV
            self._export_csv_fallback(
                orders_enriched, feasibility_df, missing_components_df
            )
            return str(self.output_dir)
        
        return str(filepath)
    
    def _export_excel_formatted(
        self,
        filepath: Path,
        orders_enriched: pd.DataFrame,
        feasibility_results: List[FeasibilityResult],
        allocation_results: pd.DataFrame,
        feasibility_df: pd.DataFrame,
        missing_components_df: pd.DataFrame
    ):
        """Crée un fichier Excel formaté avec tous les onglets"""
        
        wb = Workbook()
        
        # Onglet 1: Tableau de bord commandes
        ws1 = wb.active
        ws1.title = "Dashboard Commandes"
        self._create_dashboard_sheet(ws1, orders_enriched, allocation_results, feasibility_results)
        
        # Onglet 2: Détail composants manquants
        ws2 = wb.create_sheet("Composants Manquants")
        self._create_missing_components_sheet(ws2, missing_components_df)
        
        # Onglet 3: Vue OF
        ws3 = wb.create_sheet("Vue OF")
        self._create_mo_sheet(ws3, feasibility_df, feasibility_results)
        
        # Onglet 4: Composants critiques
        ws4 = wb.create_sheet("Composants Critiques")
        self._create_critical_components_sheet(ws4, missing_components_df)
        
        # Onglet 5: KPIs
        ws5 = wb.create_sheet("KPIs")
        self._create_kpi_sheet(ws5, allocation_results)
        
        wb.save(filepath)
    
    def _create_dashboard_sheet(
        self,
        ws,
        orders_enriched: pd.DataFrame,
        allocation_results: pd.DataFrame,
        feasibility_results: List[FeasibilityResult]
    ):
        """Crée l'onglet tableau de bord principal"""
        
        # Construire le DataFrame final
        dashboard_df = self._build_dashboard_df(
            orders_enriched, allocation_results, feasibility_results
        )
        
        # Écrire les données
        self._write_dataframe(ws, dashboard_df)
        
        # Appliquer le formatage conditionnel
        self._apply_status_formatting(ws, len(dashboard_df))
    
    def _build_dashboard_df(
        self,
        orders_enriched: pd.DataFrame,
        allocation_results: pd.DataFrame,
        feasibility_results: List[FeasibilityResult]
    ) -> pd.DataFrame:
        """Construit le DataFrame du tableau de bord"""
        alloc_map = {
            (row["order_id"], row["line_id"]): row
            for _, row in allocation_results.iterrows()
        }
        feas_map = {
            (result.order_id, result.line_id): result
            for result in feasibility_results
        }
        
        rows = []
        for _, order in orders_enriched.iterrows():
            key = (order.get("order_id", ""), order.get("line_id", ""))
            alloc = alloc_map.get(key)
            feas = feas_map.get(key)

            mo_number = None if alloc is None else alloc.get("mo_number")
            allocation_type = "" if alloc is None else alloc.get("allocation_type", "")
            allocation_status = "red" if alloc is None else alloc.get("allocation_status", "red")

            if feas is not None:
                ratio_instant = feas.ratio_instant
                ratio_projected = feas.ratio_projected
                status_emoji = feas.status_emoji
                missing_count = feas.missing_count
                sf_pf = ", ".join(feas.sf_pf_status.values()) if feas.sf_pf_status else ""
            else:
                if allocation_status == "green":
                    status_emoji = "🟢"
                elif allocation_status == "orange":
                    status_emoji = "🟠"
                else:
                    status_emoji = "🔴"
                coverage = 0.0 if alloc is None else float(alloc.get("allocation_coverage", 0.0) or 0.0)
                ratio_instant = coverage
                ratio_projected = coverage
                missing_count = 0
                sf_pf = ""
            
            rows.append({
                "N° Commande": order.get("order_id", ""),
                "Ligne": order.get("line_id", ""),
                "Client": order.get("customer", ""),
                "Article": order.get("item_code", ""),
                "Désignation": order.get("item_desc", ""),
                "Quantité": order.get("quantity", 0),
                "Date expédition": order.get("shipment_date", ""),
                "Date besoin prod.": order.get("production_need_date", ""),
                "Type": allocation_type,
                "OF associé": mo_number if pd.notna(mo_number) else "Stock",
                "Statut": status_emoji,
                "Ratio instant": f"{ratio_instant:.0%}",
                "Ratio projeté": f"{ratio_projected:.0%}",
                "Composants manquants": missing_count,
                "SF*/PF* associés": sf_pf
            })
        
        return pd.DataFrame(rows)
    
    def _create_missing_components_sheet(
        self,
        ws,
        missing_df: pd.DataFrame
    ):
        """Crée l'onglet détail composants manquants"""
        
        if missing_df.empty:
            ws.append(["Aucun composant manquant"])
            return
        
        # Renommer colonnes pour affichage
        display_df = missing_df.rename(columns={
            "mo_number": "N° OF",
            "component_code": "Composant",
            "component_desc": "Désignation",
            "required_qty": "Quantité requise",
            "atp_instant": "ATP instantané",
            "atp_projected": "ATP projeté",
            "gap_qty": "Écart",
            "next_receipt_date": "Prochaine réception",
            "next_receipt_qty": "Qté réception"
        })
        
        self._write_dataframe(ws, display_df)
    
    def _create_mo_sheet(
        self,
        ws,
        feasibility_df: pd.DataFrame,
        feasibility_results: List[FeasibilityResult]
    ):
        """Crée l'onglet vue OF pour les ateliers"""
        
        if feasibility_df.empty:
            ws.append(["Aucun OF analysé"])
            return
        
        # Préparer les données
        display_df = feasibility_df.rename(columns={
            "mo_number": "N° OF",
            "item_code": "Article produit",
            "quantity": "Quantité planifiée",
            "production_need_date": "Date besoin",
            "ratio_instant": "Ratio instant",
            "ratio_projected": "Ratio projeté",
            "status_emoji": "Statut",
            "missing_count": "Composants manquants"
        })
        
        # Ajouter colonne action recommandée
        actions = []
        for r in feasibility_results:
            if r.status == "green":
                actions.append("Lançable")
            elif r.status == "orange":
                actions.append("Attendre réceptions")
            else:
                actions.append("Relancer appro")
        
        display_df = display_df.copy()
        display_df["Action recommandée"] = actions
        
        self._write_dataframe(ws, display_df)
    
    def _create_critical_components_sheet(
        self,
        ws,
        missing_df: pd.DataFrame
    ):
        """Crée l'onglet composants critiques"""
        
        if missing_df.empty:
            ws.append(["Aucun composant critique"])
            return
        
        # Agréger par composant
        critical_df = missing_df.groupby("component_code").agg({
            "component_desc": "first",
            "required_qty": "sum",
            "atp_projected": "first",
            "gap_qty": "sum",
            "mo_number": "count"
        }).reset_index()
        
        critical_df.columns = [
            "Composant", "Désignation", "Besoin total",
            "ATP projeté", "Écart total", "OF impactés"
        ]
        
        # Trier par écart décroissant
        critical_df = critical_df.sort_values("Écart total", ascending=False)
        
        self._write_dataframe(ws, critical_df)
    
    def _create_kpi_sheet(
        self,
        ws,
        allocation_results: pd.DataFrame,
    ):
        """Crée l'onglet KPIs"""
        total_orders = len(allocation_results)
        green_count = int((allocation_results["allocation_status"] == "green").sum()) if total_orders else 0
        orange_count = int((allocation_results["allocation_status"] == "orange").sum()) if total_orders else 0
        red_count = int((allocation_results["allocation_status"] == "red").sum()) if total_orders else 0
        
        kpis = [
            ("Indicateurs Globaux", ""),
            ("", ""),
            ("Total commandes analysées", total_orders),
            ("Commandes couvertes à 100%", f"{green_count} ({green_count/total_orders:.1%})" if total_orders > 0 else "0"),
            ("Commandes couvertes à date (orange)", f"{orange_count} ({orange_count/total_orders:.1%})" if total_orders > 0 else "0"),
            ("Commandes non couvrables (rouge)", f"{red_count} ({red_count/total_orders:.1%})" if total_orders > 0 else "0"),
            ("", ""),
            ("Composants critiques", red_count),
            ("OF avec manque", red_count),
        ]
        
        for row in kpis:
            ws.append(row)
    
    def _write_dataframe(self, ws, df: pd.DataFrame):
        """Écrit un DataFrame dans une feuille avec formatage"""
        
        # Style header
        header_fill = PatternFill(
            start_color=self.COLORS["header"],
            end_color=self.COLORS["header"],
            fill_type="solid"
        )
        header_font = Font(bold=True, color=self.COLORS["header_font"])
        
        # Écrire header
        ws.append(df.columns.tolist())
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        
        # Écrire données
        for row in dataframe_to_rows(df, index=False, header=False):
            ws.append(row)
    
    def _apply_status_formatting(self, ws, row_count: int):
        """Applique le formatage conditionnel pour les statuts"""
        
        green_fill = PatternFill(
            start_color=self.COLORS["green"],
            end_color=self.COLORS["green"],
            fill_type="solid"
        )
        orange_fill = PatternFill(
            start_color=self.COLORS["orange"],
            end_color=self.COLORS["orange"],
            fill_type="solid"
        )
        red_fill = PatternFill(
            start_color=self.COLORS["red"],
            end_color=self.COLORS["red"],
            fill_type="solid"
        )
        
        # Colonne statut (K = 11ème colonne)
        status_col = 11
        
        for row_idx in range(2, row_count + 2):
            cell = ws.cell(row=row_idx, column=status_col)
            value = str(cell.value) if cell.value else ""
            
            if "🟢" in value:
                cell.fill = green_fill
            elif "🟠" in value:
                cell.fill = orange_fill
            elif "🔴" in value:
                cell.fill = red_fill
    
    def _export_csv_fallback(
        self,
        orders_enriched: pd.DataFrame,
        feasibility_df: pd.DataFrame,
        missing_components_df: pd.DataFrame
    ):
        """Export en CSV si openpyxl non disponible"""
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        orders_enriched.to_csv(
            self.output_dir / f"commandes_{timestamp}.csv",
            index=False
        )
        
        feasibility_df.to_csv(
            self.output_dir / f"faisabilite_{timestamp}.csv",
            index=False
        )
        
        missing_components_df.to_csv(
            self.output_dir / f"composants_manquants_{timestamp}.csv",
            index=False
        )
