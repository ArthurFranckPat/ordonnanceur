# Apparieur Commandes -> OF
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .calendar import BusinessCalendar
from .config import Config


@dataclass
class MatchResult:
    """Resultat d'appariement commande -> OF."""

    order_id: str
    line_id: str
    item_code: str
    quantity_ordered: float
    production_need_date: Optional[date]
    matched_mo_number: Optional[str]
    mo_end_date: Optional[date]
    mo_remaining_capacity: float
    match_type: str  # mto, mo_matched, stock_only, no_supply


class OrderMOMatcher:
    """Apparie les lignes de commandes aux OF candidats."""

    CANDIDATE_STATUSES = {
        "Planned",
        "Suggested",
        "Firm",
        "Released",
        "Planifie",
        "Planifiee",
        "Planifiee",
        "Planifie",
        "Suggere",
        "Suggered",
        "Ferme",
        "Lance",
    }

    def __init__(self, config: Config, calendar: BusinessCalendar):
        self.config = config
        self.calendar = calendar

    def enrich_orders(
        self,
        orders_df: pd.DataFrame,
        mo_candidates_df: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, List[MatchResult]]:
        """Calcule les dates de besoin et enrichit les commandes avec un OF prefere."""
        orders = self._with_need_dates(orders_df)
        mo_with_capacity = self._prepare_mo_candidates(mo_candidates_df)

        results: List[MatchResult] = []
        for _, order in orders.iterrows():
            results.append(self._match_single_order(order, mo_with_capacity))

        enriched_orders = self._merge_results(orders, results)
        return enriched_orders, results

    def _with_need_dates(self, orders_df: pd.DataFrame) -> pd.DataFrame:
        """Calcule la date de besoin production si absente."""
        df = orders_df.copy()
        if "production_need_date" in df.columns:
            return df

        need_dates: List[Optional[date]] = []
        for _, row in df.iterrows():
            shipment_date = row.get("shipment_date")
            if pd.isna(shipment_date):
                need_dates.append(None)
                continue

            ship_date = shipment_date.date() if hasattr(shipment_date, "date") else shipment_date
            need_dates.append(
                self.calendar.subtract_business_days(
                    ship_date,
                    self.config.production_offset_days,
                )
            )

        df["production_need_date"] = need_dates
        return df

    def _prepare_mo_candidates(self, mo_candidates_df: pd.DataFrame) -> pd.DataFrame:
        """Normalise la capacite restante et les types de dates."""
        if mo_candidates_df.empty:
            return pd.DataFrame()

        df = mo_candidates_df.copy()

        if "end_date" in df.columns:
            df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")

        if "remaining_capacity" not in df.columns:
            if "planned_qty" in df.columns:
                allocated = df["allocated_qty"] if "allocated_qty" in df.columns else 0
                df["remaining_capacity"] = df["planned_qty"] - allocated
            elif "quantity" in df.columns:
                df["remaining_capacity"] = df["quantity"]
            else:
                df["remaining_capacity"] = 0.0

        if "status" in df.columns:
            normalized = df["status"].fillna("").astype(str).str.strip()
            df = df[normalized.isin(self.CANDIDATE_STATUSES) | normalized.eq("")]

        return df

    def _match_single_order(
        self,
        order: pd.Series,
        mo_with_capacity: pd.DataFrame,
    ) -> MatchResult:
        """Selectionne un OF prefere pour la commande, sans consommer la capacite."""
        order_id = str(order.get("order_id", ""))
        line_id = str(order.get("line_id", ""))
        item_code = str(order.get("item_code", ""))
        quantity_ordered = float(order.get("quantity", 0) or 0)
        production_need_date = order.get("production_need_date")

        direct_mo = order.get("mo_number")
        if pd.notna(direct_mo) and direct_mo:
            mo_info = self._get_mo_info(mo_with_capacity, str(direct_mo))
            mo_end_date: Optional[date] = None
            mo_remaining_capacity = 0.0
            if mo_info is not None:
                raw_end_date = mo_info["end_date"]
                mo_end_date = raw_end_date if isinstance(raw_end_date, date) else None
                raw_capacity = mo_info["remaining_capacity"]
                if isinstance(raw_capacity, (int, float)):
                    mo_remaining_capacity = float(raw_capacity)
            return MatchResult(
                order_id=order_id,
                line_id=line_id,
                item_code=item_code,
                quantity_ordered=quantity_ordered,
                production_need_date=production_need_date,
                matched_mo_number=str(direct_mo),
                mo_end_date=mo_end_date,
                mo_remaining_capacity=mo_remaining_capacity,
                match_type="mto",
            )

        if mo_with_capacity.empty:
            return MatchResult(
                order_id=order_id,
                line_id=line_id,
                item_code=item_code,
                quantity_ordered=quantity_ordered,
                production_need_date=production_need_date,
                matched_mo_number=None,
                mo_end_date=None,
                mo_remaining_capacity=0.0,
                match_type="stock_only",
            )

        candidates = mo_with_capacity[mo_with_capacity["item_code"] == item_code].copy()
        if candidates.empty:
            return MatchResult(
                order_id=order_id,
                line_id=line_id,
                item_code=item_code,
                quantity_ordered=quantity_ordered,
                production_need_date=production_need_date,
                matched_mo_number=None,
                mo_end_date=None,
                mo_remaining_capacity=0.0,
                match_type="stock_only",
            )

        if production_need_date is not None and "end_date" in candidates.columns:
            candidates = candidates[
                candidates["end_date"].isna()
                | (candidates["end_date"].dt.date <= production_need_date)
            ]

        if candidates.empty:
            return MatchResult(
                order_id=order_id,
                line_id=line_id,
                item_code=item_code,
                quantity_ordered=quantity_ordered,
                production_need_date=production_need_date,
                matched_mo_number=None,
                mo_end_date=None,
                mo_remaining_capacity=0.0,
                match_type="no_supply",
            )

        candidates = candidates.sort_values(
            by=["end_date", "remaining_capacity"],
            ascending=[True, False],
            na_position="last",
        )
        best_match = candidates.iloc[0]

        return MatchResult(
            order_id=order_id,
            line_id=line_id,
            item_code=item_code,
            quantity_ordered=quantity_ordered,
            production_need_date=production_need_date,
            matched_mo_number=best_match.get("mo_number"),
            mo_end_date=best_match.get("end_date").date() if pd.notna(best_match.get("end_date")) else None,
            mo_remaining_capacity=float(best_match.get("remaining_capacity", 0.0) or 0.0),
            match_type="mo_matched",
        )

    def _get_mo_info(self, mo_df: pd.DataFrame, mo_number: str) -> Optional[Dict[str, object]]:
        """Recupere les infos d'un OF par son numero."""
        if mo_df.empty:
            return None

        match = mo_df[mo_df["mo_number"] == mo_number]
        if match.empty:
            return None

        row = match.iloc[0]
        end_date = row.get("end_date")
        return {
            "end_date": end_date.date() if pd.notna(end_date) and hasattr(end_date, "date") else end_date,
            "remaining_capacity": float(row.get("remaining_capacity", 0.0) or 0.0),
        }

    def _merge_results(self, orders_df: pd.DataFrame, results: List[MatchResult]) -> pd.DataFrame:
        """Injecte les informations d'appariement dans les commandes."""
        df = orders_df.copy()
        result_map = {(r.order_id, r.line_id): r for r in results}

        matched_mo_numbers: List[Optional[str]] = []
        mo_end_dates: List[Optional[date]] = []
        mo_capacities: List[float] = []
        match_types: List[str] = []

        for _, row in df.iterrows():
            key = (str(row.get("order_id", "")), str(row.get("line_id", "")))
            result = result_map[key]
            matched_mo_numbers.append(result.matched_mo_number)
            mo_end_dates.append(result.mo_end_date)
            mo_capacities.append(result.mo_remaining_capacity)
            match_types.append(result.match_type)

        df["matched_mo_number"] = matched_mo_numbers
        df["matched_mo_end_date"] = mo_end_dates
        df["matched_mo_remaining_capacity"] = mo_capacities
        df["match_type"] = match_types
        return df
