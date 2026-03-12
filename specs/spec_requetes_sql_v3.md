# Spécification Requêtes SQL V3 - Outil d'Ordonnancement OF

**Version** : 3.0  
**Date** : Mars 2026  
**Compatibilité** : Sage X3 v12

---

## 1. VUE D'ENSEMBLE

### 1.1 Fichiers CSV requis
| # | Fichier | Description | Tables source |
|---|--------|-------------|---------------|
| 1 | commandes_clients.csv | Lignes commandes fermes dans horizon | SORDERQ, SORDER |
| 2 | stock_atp.csv | Stock + allocations par article-site | ITMMVT, STOALL |
| 3 | receptions_attendues.csv | OA en cours + OF fermes à recevoir | PORDERQ, MFGHEAD |
| 4 | of_candidats.csv | OF planifiés/suggérés + capacité restante | MFGHEAD |
| 5 | composants_of.csv | Composants (nomenclature) par OF | MFGMAT, ITMBOM |
| 6 | referentiel_articles.csv | Caractéristiques articles | ITMMASTER |
| 7 | calendrier_fermetures.csv | Fermetures Aereco (paramétrable) | Excel manuel |

---

## 2. REQUÊTE 1 : COMMANDES CLIENTS

### Objectif
Extraire les lignes de commandes clients fermes dans l'horizon de planification.

### Tables sources
- `SORDERQ` (lignes commandes) - `SORDER` (en-têtes commandes)

### Colonnes requises
```sql
SELECT
    -- Identifiants
    SOQ.SOHNUM AS commande_numero,
    SOQ.SOPLIN AS ligne_numero,
    SOQ.ITMREF AS article,
    ITM.ITMDES AS article_designation,
    
    -- Quantités et dates
    SOQ.QTYSTU AS quantite_commandee,
    SOQ.ALLQTYSTU AS quantite_allouee,
    SOQ.SHIDAT AS date_expedition,
    
    -- Client
    SOH.BPCORD AS client_code,
    BPC.BPCNAM AS client_nom,
    
    -- Priorité et statut
    SOQ.SOQSTA AS statut_ligne,
    SOH.ORDSTA AS statut_commande,
    COALESCE(SOQ.DLVPIO, 3) AS priorite,  -- 1=Très urgent, 2=Urgent, 3=Normal
    
    -- Type ligne (contremarque vs stock)
    SOQ.MTOFLG AS is_contremarque,
    SOQ.MTOREF AS of_contremarque,  -- Lien vers OF si contremarque
    
    -- Site
    SOQ.STOFCY AS site
    
FROM SORDERQ SOQ
INNER JOIN SORDER SOH ON SOQ.SOHNUM = SOH.SOHNUM
INNER JOIN ITMMASTER ITM ON SOQ.ITMREF = ITM.ITMREF
INNER JOIN BPARTNER BPC ON SOH.BPCORD = BPC.BPCNUM

WHERE 1=1
    -- Filtres
    AND SOQ.SHIDAT BETWEEN GETDATE() AND DATEADD(WEEK, @HORIZON_WEEKS, GETDATE())
    AND SOQ.SOQSTA IN (1, 2)  -- Statuts "open" (adapter selon environnement)
    AND SOH.ORDSTA IN (1, 2)  -- Commandes fermes (adapter selon environnement)
    AND SOQ.STOFCY = @SITE_CODE  -- Monosite
    
ORDER BY 
    SOQ.SHIDAT ASC,
    priorite ASC,
    SOQ.QTYSTU ASC
```

### Points d'attention
1. **Valeurs statut** : Les codes MFGSTA/SOQSTA/ORDSTA varient selon paramétrage. À valider dans l'environnement Aereco.
2. **Priorité** : Le champ DLVPIO peut ne pas exister ou avoir des valeurs différentes. Prévoir valeur par défaut = 3 (Normal).
3. **Contremarque** : MTOFLG indique si ligne en contremarque. MTOREF contient le n° OF lié.
4. **Horizon** : Paramétrable (défaut = 8 semaines).

---

## 3. REQUÊTE 2 : STOCK ET ATP

### Objectif
Calculer le stock disponible (ATP) par article-site.

### Tables sources
- `ITMMVT` (totaux article-site)
- `STOALL` (allocations détaillées) - optionnel pour détail

### Colonnes requises
```sql
SELECT
    STO.ITMREF AS article,
    STO.STOFCY AS site,
    
    -- Stock physique
    STO.PHYSTO AS stock_physique,
    
    -- Alloué (commandes + OF fermes)
    STO.CUMALLQTY AS stock_alloue,
    
    -- En-cours production
    STO.CUMWIPQTY AS stock_en_cours,
    
    -- Calcul stock disponible
    (STO.PHYSTO - STO.CUMALLQTY) AS stock_disponible,
    
    -- Info article
    ITM.ITMDES AS designation,
    ITM.TSICOD AS type_appro,  -- Achat vs Fabrication
    ITM.TCLCOD AS categorie     -- Pour identifier SF*/PF*
    
FROM ITMMVT STO
INNER JOIN ITMMASTER ITM ON STO.ITMREF = ITM.ITMREF

WHERE STO.STOFCY = @SITE_CODE
AND ITM.TSICOD IN ('ACH', 'FAB')  -- Types achat/fabrication (adapter selon environnement)
```

### Points d'attention
1. **Champ allocations** : `CUMALLQTY` vs `ALLSTO` selon version. Vérifier dans l'environnement Aereco.
2. **Type appro** : `TSICOD` peut avoir des valeurs différentes (ex: 'PUR', 'MFG'). Adapter.
3. **Catégorie** : `TCLCOD` contient la catégorie article (SF*, PF*, etc.). Utilisé pour règle non-bloquant.

---

## 4. REQUÊTE 3 : RÉCEPTIONS ATTENDUES

### Objectif
Identifier les réceptions prévues : OA en cours + OF fermes à recevoir.

### Tables sources
- `PORDERQ` (lignes OA)
- `PORDERH` (en-têtes OA) - `MFGHEAD` (en-têtes OF)

### Colonnes requises - OA
```sql
-- Réceptions OA
SELECT
    'OA' AS type_reception,
    POQ.POQSEQ AS reception_id,
    POQ.ITMREF AS article,
    POQ.STOFCY AS site,
    (POQ.QTYSTU - POQ.RCPQTY) AS quantite_attendue,
    POQ.RCPDAT AS date_prevue,
    POH.POHNUM AS numero_oa,
    POH.BPSNUM AS fournisseur
    
FROM PORDERQ POQ
INNER JOIN PORDERH POH ON POQ.POQNUM = POH.POQNUM

WHERE 1=1
    AND POQ.RCPDAT BETWEEN GETDATE() AND DATEADD(WEEK, @HORIZON_WEEKS, GETDATE())
    AND (POQ.QTYSTU - POQ.RCPQTY) > 0  -- Reste à recevoir > 0
    AND POQ.STOFCY = @SITE_CODE

UNION ALL

-- Réceptions OF (OF fermes/lancés qui vont produire)
SELECT
    'OF' AS type_reception,
    MFG.MFGNUM AS reception_id,
    MFG.ITMREF AS article,
    MFG.MFGFCY AS site,
    (MFG.EXTQTY - MFG.CPLQTY) AS quantite_attendue,  -- Quantité restant à produire
    MFG.ENDDAT AS date_prevue,
    MFG.MFGNUM AS numero_of,
    NULL AS fournisseur
    
FROM MFGHEAD MFG

WHERE 1=1
    AND MFG.MFGSTA >= @STATUT_FERME  -- OF fermes ou au-delà (adapter selon environnement)
    AND (MFG.EXTQTY - MFG.CPLQTY) > 0  -- Reste à produire > 0
    AND MFG.ENDDAT BETWEEN GETDATE() AND DATEADD(WEEK, @HORIZON_WEEKS, GETDATE())
    AND MFG.MFGFCY = @SITE_CODE

ORDER BY date_prevue ASC
```

### Points d'attention
1. **Statut OF ferme** : La valeur de `MFGSTA` pour "Ferme" varie. À valider (souvent 3 ou 4).
2. **Quantité restante** : `EXTQTY - CPLQTY` ou champ dédié selon paramétrage.
3. **Date prévue OA** : `RCPDAT` peut ne pas être fiable. Certains environnements utilisent une date calculée.

---

## 5. REQUÊTE 4 : OF CANDIDATS

### Objectif
Identifier les OF qui peuvent servir les commandes (planifiés + capacité restante).

### Tables sources
- `MFGHEAD` (en-têtes OF)

### Colonnes requises
```sql
SELECT
    MFG.MFGNUM AS of_numero,
    MFG.ITMREF AS article_produit,
    ITM.ITMDES AS article_designation,
    
    -- Quantités
    MFG.EXTQTY AS quantite_planifiee,
    MFG.CPLQTY AS quantite_produite,
    (MFG.EXTQTY - MFG.CPLQTY) AS capacite_restante,
    
    -- Dates
    MFG.STRDAT AS date_debut,
    MFG.ENDDAT AS date_fin,
    
    -- Statut et allocation
    MFG.MFGSTA AS statut,
    MFG.ALLSTA AS statut_allocation,
    MFG.MFGPIO AS priorite,
    
    -- Site
    MFG.MFGFCY AS site,
    
    -- Type (contremarque ou non)
    MFG.MTOFLG AS is_contremarque,
    MFG.MTOREF AS commande_liee
    
FROM MFGHEAD MFG
INNER JOIN ITMMASTER ITM ON MFG.ITMREF = ITM.ITMREF

WHERE 1=1
    AND MFG.MFGFCY = @SITE_CODE
    AND (
        -- OF planifiés ou suggérés
        MFG.MFGSTA IN (@STATUT_PLANIFIE, @STATUT_SUGGERE)
        -- OU OF fermes avec capacité restante
        OR (MFG.MFGSTA >= @STATUT_FERME AND (MFG.EXTQTY - MFG.CPLQTY) > 0)
    )
    AND MFG.ENDDAT BETWEEN GETDATE() AND DATEADD(WEEK, @HORIZON_WEEKS + 2, GETDATE())
    AND MFG.CLOFLG = 0  -- Non clôturé (adapter selon environnement)

ORDER BY 
    MFG.ENDDAT ASC,
    MFG.MFGPIO ASC
```

### Points d'attention
1. **Valeurs statut** : Planifié, Suggéré, Ferme - varient selon environnement. Tester.
2. **CLOFLG** : Indique si OF clôturé. Peut être différent ou absent.
3. **Capacité restante** : Pour OF fermes, vérifier s'ils sont déjà "alloués" à des commandes spécifiques.
4. **Contremarque** : MTOFLG indique si OF en contremarque (lié à commande).

---

## 6. REQUÊTE 5 : COMPOSANTS OF

### Objectif
Extraire la nomenclature (composants) pour chaque OF.

### Tables sources
- `MFGMAT` (composants OF) - `ITMBOM` (nomenclature standard) - fallback

### Colonnes requises
```sql
SELECT
    MFM.MFGNUM AS of_numero,
    MFM.MFGLIN AS ligne_numero,
    MFM.ITMREF AS composant,
    ITM.ITMDES AS composant_designation,
    
    -- Quantités
    MFM.RETQTY AS quantite_requise,
    MFM.ALLQTY AS quantite_allouee,
    (MFM.RETQTY - MFM.ALLQTY) AS quantite_manquante,
    
    -- Dates
    MFM.RETDAT AS date_besoin,
    
    -- Statut allocation
    MFM.ALLSTA AS statut_allocation,
    MFM.MATSTA AS statut_matiere,
    
    -- Info composant
    ITM.TSICOD AS type_appro,
    ITM.TCLCOD AS categorie,
    
    -- Opération (pour délai)
    MFM.BOMOPE AS operation,
    MFM.BOMOFS AS operation_leadtime
    
FROM MFGMAT MFM
INNER JOIN ITMMASTER ITM ON MFM.ITMREF = ITM.ITMREF

WHERE MFM.MFGNUM IN (
    SELECT MFGNUM FROM MFGHEAD 
    WHERE MFGFCY = @SITE_CODE
    AND MFGSTA IN (@STATUT_PLANIFIE, @STATUT_SUGGERE, @STATUT_FERME)
)

ORDER BY MFM.MFGNUM, MFM.MFGLIN
```

### Points d'attention
1. **Nomenclature multi-niveaux** : Cette requête donne les composants directs. Pour les sous-ensembles fabriqués, il faut itérer.
2. **Type appro** : Distingue acheté (bloquant) vs fabriqué (SF*/PF* non-bloquant).
3. **Date besoin** : RETDAT peut différer de la date début OF selon opération.
4. **Fallback nomenclature** : Si OF sans composants dans MFGMAT, utiliser ITMBOM.

---

## 7. REQUÊTE 6 : RÉFÉRENTIEL ARTICLES

### Objectif
Caractéristiques des articles pour classification et règles.

### Tables sources
- `ITMMASTER` (articles)

### Colonnes requises
```sql
SELECT
    ITM.ITMREF AS article,
    ITM.ITMDES AS designation,
    ITM.TSICOD AS type_appro,
    ITM.TCLCOD AS categorie,
    ITM.STU AS unite_stock,
    ITM.PURFLG AS is_achat,
    ITM.MFGFLG AS is_fabrication,
    
    -- Lead times
    ITM.PURLEAD AS leadtime_achat,
    ITM.MFGLEAD AS leadtime_fabrication,
    
    -- Lot size
    ITM.PURLOT AS lot_achat,
    ITM.MFGLOT AS lot_fabrication,
    
    -- Statut
    ITM.ITMSTA AS statut_article
    
FROM ITMMASTER ITM

WHERE ITM.ITMSTA = 1  -- Actif (adapter selon environnement)
AND (ITM.PURFLG = 1 OR ITM.MFGFLG = 1)  -- Acheté OU fabriqué
```

### Points d'attention
1. **Type appro** : `TSICOD` peut avoir des valeurs personnalisées. Vérifier correspondance SF*/PF*.
2. **Catégorie** : `TCLCOD` utilisé pour identifier les articles SF* et PF* (non-bloquants).
3. **Lead times** : Peuvent être dans ITMFACILIT (article-site) au lieu de ITMMASTER.

---

## 8. REQUÊTE 7 : CALENDRIER FERMETURES

### Objectif
Fermetures spécifiques Aereco pour calcul jours ouvrés.

### Source
Fichier Excel paramétrable (pas de table X3).

### Format requis
```csv
date,type,description
2026-04-06,fermeture,Lundi de Pâques
2026-05-01,fermeture,Fête du travail
2026-05-08,fermeture,Armistice 1945
2026-07-15,fermeture,Pont juillet
2026-08-15,conges_usine,Congés août
2026-11-01,fermeture,Toussaint
2026-11-11,fermeture,Armistice 1918
2026-12-25,fermeture,Noël
```

### Points d'attention
1. **Distinction types** : `fermeture` vs `conges_usine` si comportement différent.
2. **Saisie** : À maintenir par l'utilisateur Supply Chain.
3. **Fériés France** : Peuvent être générés automatiquement par Python (bibliothèque `holidays`).

---

## 9. POINTS OUVERTS POUR AGENT SQL

### 9.1 À valider dans l'environnement Aereco
1. **Valeurs MFGSTA** : Codes exacts pour Suggéré, Planifié, Ferme, Lancé
2. **Valeurs SOQSTA/ORDSTA** : Codes exacts pour statuts commandes
3. **Champ priorité** : Nom exact et valeurs (DLVPIO ? autre ?)
4. **Champ allocations stock** : `CUMALLQTY` vs `ALLSTO` vs autre
5. **Champ catégorie SF*/PF*** : `TCLCOD` ou autre champ personnalisé
6. **Champ contremarque** : `MTOFLG` ou autre indicateur

### 9.2 À clarifier fonctionnellement
1. **Exclure OF en contremarque** de l'appariement automatique ? (probablement oui)
2. **Tolérance date** : ENDDAT OF ≤ (SHIDAT - 2 JO) + combien de jours ?
3. **OF avec allocation existante** : Comment déterminer la capacité restante ?
4. **Multi-niveaux** : Profondeur maximale à explorer (défaut = 5 niveaux)

### 9.3 Optimisations possibles
1. **Vue matérialisée** pour ATP (calculé une fois au lieu de requête complexe)
2. **Index** sur SHIDAT, ITMREF, MFGSTA si volumes importants
3. **Partition** par site si multi-sites futur

---

## 10. SCRIPT GÉNÉRATION CSV (Python)

```python
import pyodbc
import pandas as pd
from datetime import datetime, timedelta

def generate_poc_csvs(connection_string, site_code='AERECO', horizon_weeks=8):
    """
    Génère les 6 fichiers CSV pour le POC à partir de Sage X3
    """
    conn = pyodbc.connect(connection_string)
    
    # 1. Commandes clients
    df_orders = pd.read_sql(f"""
        SELECT ... (requête 1)
    """, conn)
    df_orders.to_csv('poc_data/commandes_clients.csv', index=False)
    
    # 2. Stock ATP
    df_stock = pd.read_sql(f"""
        SELECT ... (requête 2)
    """, conn)
    df_stock.to_csv('poc_data/stock_atp.csv', index=False)
    
    # 3. Réceptions attendues
    df_receipts = pd.read_sql(f"""
        SELECT ... (requête 3)
    """, conn)
    df_receipts.to_csv('poc_data/receptions_attendues.csv', index=False)
    
    # 4. OF candidats
    df_mos = pd.read_sql(f"""
        SELECT ... (requête 4)
    """, conn)
    df_mos.to_csv('poc_data/of_candidats.csv', index=False)
    
    # 5. Composants OF
    df_components = pd.read_sql(f"""
        SELECT ... (requête 5)
    """, conn)
    df_components.to_csv('poc_data/composants_of.csv', index=False)
    
    # 6. Référentiel articles
    df_items = pd.read_sql(f"""
        SELECT ... (requête 6)
    """, conn)
    df_items.to_csv('poc_data/referentiel_articles.csv', index=False)
    
    conn.close()
    print("CSV générés dans poc_data/")
```

---

**Document vivant** - Adapter les requêtes selon retour agent SQL
