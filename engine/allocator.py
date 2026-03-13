# Allocateur sequentiel par priorite
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .atp_calculator import ATPCalculator
from .calendar import BusinessCalendar
from .config import Config


@dataclass
class AllocationResult:
    """Resultat d'allocation pour une ligne de commande."""

    order_id: str
    line_id: str
    customer: str
    item_code: str
    quantity_ordered: float
    quantity_allocated: float
    allocation_type: str  # stock, mo, partial, none
    match_type: str
    mo_number: Optional[str]
    production_need_date: Optional[date]
    allocation_status: str  # green, orange, red
    atp_consumed: Dict[str, float]


class Allocator:
    """Gere l'allocation sequentielle du stock et des OF."""

    def __init__(
        self,
        config: Config,
        calendar: BusinessCalendar,
        atp_calculator: ATPCalculator,
    ):
        self.config = config
        self.calendar = calendar
        self.atp_calculator = atp_calculator

    def allocate_orders(
        self,
        orders_df: pd.DataFrame,
        atp_df: pd.DataFrame,
        mo_candidates_df: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, List[AllocationResult]]:
        """Alloue les commandes sequentiellement par date de besoin puis priorite."""
        orders = self._sort_by_priority(orders_df)
        item_stock_tracker = self._build_item_stock_tracker(atp_df)
        mo_capacity_tracker = self._build_mo_capacity_tracker(mo_candidates_df)

        results: List[AllocationResult] = []
        for _, order in orders.iterrows():
            result = self._allocate_single_order(order, item_stock_tracker, mo_capacity_tracker)
            results.append(result)

        return self._results_to_dataframe(results), results

    def _sort_by_priority(self, orders_df: pd.DataFrame) -> pd.DataFrame:
        """Trie selon la spec: date besoin, priorite client, identifiant."""
        df = orders_df.copy()
        if "production_need_date" not in df.columns:
            raise ValueError("production_need_date doit etre calculee avant allocation")

        df["sort_date"] = df["production_need_date"].apply(
            lambda value: value if value is not None else date.max
        )
        df["sort_priority"] = df["priority"] if "priority" in df.columns else 999
        df["sort_priority"] = df["sort_priority"].fillna(999)
        df["sort_order"] = df["order_id"].astype(str) + "-" + df.get("line_id", "").astype(str)

        return df.sort_values(
            by=["sort_date", "sort_priority", "sort_order"],
            ascending=[True, True, True],
        )

    def _build_item_stock_tracker(self, atp_df: pd.DataFrame) -> Dict[str, float]:
        """Construit le stock instantane disponible par article."""
        tracker: Dict[str, float] = {}
        for _, row in atp_df.iterrows():
            tracker[str(row["item_code"])] = float(row.get("atp_instant", 0.0) or 0.0)
        return tracker

    def _build_mo_capacity_tracker(self, mo_candidates_df: pd.DataFrame) -> Dict[str, float]:
        """Construit la capacite restante consommee au fil des allocations."""
        tracker: Dict[str, float] = {}
        if mo_candidates_df.empty:
            return tracker

        for _, row in mo_candidates_df.iterrows():
            mo_number = row.get("mo_number")
            if pd.isna(mo_number) or not mo_number:
                continue
            tracker[str(mo_number)] = float(row.get("remaining_capacity", 0.0) or 0.0)
        return tracker

    def _allocate_single_order(
        self,
        order: pd.Series,
        item_stock_tracker: Dict[str, float],
        mo_capacity_tracker: Dict[str, float],
    ) -> AllocationResult:
        """Alloue une commande en priorisant le stock instantane puis l'OF deja apparie."""
        order_id = str(order.get("order_id", ""))
        line_id = str(order.get("line_id", ""))
        customer = str(order.get("customer", ""))
        item_code = str(order.get("item_code", ""))
        quantity_needed = float(order.get("quantity", 0.0) or 0.0)
        production_need_date = order.get("production_need_date")
        preferred_mo = order.get("matched_mo_number")
        match_type = str(order.get("match_type", ""))

        if match_type == "mto" and pd.notna(preferred_mo) and preferred_mo:
            mo_number = str(preferred_mo)
            if mo_number in mo_capacity_tracker and mo_capacity_tracker[mo_number] >= quantity_needed:
                mo_capacity_tracker[mo_number] -= quantity_needed
            return AllocationResult(
                order_id=order_id,
                line_id=line_id,
                customer=customer,
                item_code=item_code,
                quantity_ordered=quantity_needed,
                quantity_allocated=quantity_needed,
                allocation_type="mo",
                match_type=match_type,
                mo_number=mo_number,
                production_need_date=production_need_date,
                allocation_status="green",
                atp_consumed={},
            )

        stock_available = item_stock_tracker.get(item_code, 0.0)
        stock_allocated = min(stock_available, quantity_needed)

        if stock_allocated >= quantity_needed:
            item_stock_tracker[item_code] = stock_available - stock_allocated
            return AllocationResult(
                order_id=order_id,
                line_id=line_id,
                customer=customer,
                item_code=item_code,
                quantity_ordered=quantity_needed,
                quantity_allocated=quantity_needed,
                allocation_type="stock",
                match_type=match_type,
                mo_number=None,
                production_need_date=production_need_date,
                allocation_status="green",
                atp_consumed={item_code: stock_allocated},
            )

        remaining_need = quantity_needed - stock_allocated
        allocated_mo_number = self._consume_preferred_mo(preferred_mo, remaining_need, mo_capacity_tracker)

        if stock_allocated > 0:
            item_stock_tracker[item_code] = stock_available - stock_allocated

        if allocated_mo_number is not None:
            allocation_type = "mo" if stock_allocated == 0 else "partial"
            allocation_status = "orange" if stock_allocated > 0 else "green"
            return AllocationResult(
                order_id=order_id,
                line_id=line_id,
                customer=customer,
                item_code=item_code,
                quantity_ordered=quantity_needed,
                quantity_allocated=quantity_needed,
                allocation_type=allocation_type,
                match_type=match_type,
                mo_number=allocated_mo_number,
                production_need_date=production_need_date,
                allocation_status=allocation_status,
                atp_consumed={item_code: stock_allocated} if stock_allocated > 0 else {},
            )

        if stock_allocated > 0:
            return AllocationResult(
                order_id=order_id,
                line_id=line_id,
                customer=customer,
                item_code=item_code,
                quantity_ordered=quantity_needed,
                quantity_allocated=stock_allocated,
                allocation_type="partial",
                match_type=match_type,
                mo_number=None,
                production_need_date=production_need_date,
                allocation_status="red",
                atp_consumed={item_code: stock_allocated},
            )

        return AllocationResult(
            order_id=order_id,
            line_id=line_id,
            customer=customer,
            item_code=item_code,
            quantity_ordered=quantity_needed,
            quantity_allocated=0.0,
            allocation_type="none",
            match_type=match_type,
            mo_number=None,
            production_need_date=production_need_date,
            allocation_status="red",
            atp_consumed={},
        )

    def _consume_preferred_mo(
        self,
        preferred_mo: object,
        remaining_need: float,
        mo_capacity_tracker: Dict[str, float],
    ) -> Optional[str]:
        """Consomme la capacite de l'OF deja apparie s'il couvre le reliquat."""
        if pd.isna(preferred_mo) or not preferred_mo or remaining_need <= 0:
            return None

        mo_number = str(preferred_mo)
        current_capacity = mo_capacity_tracker.get(mo_number, 0.0)
        if current_capacity < remaining_need:
            return None

        mo_capacity_tracker[mo_number] = current_capacity - remaining_need
        return mo_number

    def _results_to_dataframe(self, results: List[AllocationResult]) -> pd.DataFrame:
        """Convertit les allocations en DataFrame."""
        rows = []
        for result in results:
            rows.append(
                {
                    "order_id": result.order_id,
                    "line_id": result.line_id,
                    "customer": result.customer,
                    "item_code": result.item_code,
                    "quantity_ordered": result.quantity_ordered,
                    "quantity_allocated": result.quantity_allocated,
                    "allocation_type": result.allocation_type,
                    "match_type": result.match_type,
                    "mo_number": result.mo_number,
                    "production_need_date": result.production_need_date,
                    "allocation_status": result.allocation_status,
                    "allocation_coverage": (
                        result.quantity_allocated / result.quantity_ordered
                        if result.quantity_ordered > 0
                        else 0.0
                    ),
                }
            )
        return pd.DataFrame(rows)
