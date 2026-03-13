# Spécification Fonctionnelle V3 - Outil d'Ordonnancement OF

**Version** : 3.0  
**Date** : Mars 2026  
**Auteur** : Équipe Supply Chain Aereco  
**Statut** : Prêt pour implémentation POC

---

## 1. RÉSUMÉ EXÉCUTIF

### Objectif
Outil d'aide à la décision pour le lancement des ordres de fabrication (OF), piloté par la demande client, garantissant qu'aucun OF n'est lancé sans couverture matière à 100%.

### Changements clés V2 → V3
| Aspect | V2 | V3 |
|--------|----|----|
| Règle de priorité | Ratio de faisabilité décroissant | Date de besoin croissante + ratio |
| SF*/PF* | Non-bloquants, forcés verts | Indépendants avec visibilité |
| Concept stock | Stock projeté à date | ATP (Available-to-Promise) |
| Mode conservateur/optimiste | Conservé (inactif) | Supprimé |
| Appariement commande→OF | Date la plus proche | Capacité restante + date |

---

## 2. PÉRIMÈTRE FONCTIONNEL

### 2.1 Entrées

#### Commandes clients
- **Source** : `SORDERQ` (lignes commandes de vente)
- **Filtres** :
  - Statut : Fermes uniquement (exclure devis/quotations)
  - Horizon : SHIDAT entre aujourd'hui et +8 semaines (paramétrable)
  - Site : Monosite Aereco

#### Stock et allocations
- **Source** : `ITMMVT` (totaux article-site), `STOALL` (allocations détaillées)
- **Concept** : ATP = Stock physique - Alloué ferme + Réceptions attendues

#### Réceptions attendues
- **OA en cours** : `PORDERQ` (lignes commandes d'achat non reçues)
- **OF fermes/lancés** : `MFGHEAD` (OF avec statut ≥ Ferme)
- **Transferts** : Si applicable

#### OF candidats
- **Source** : `MFGHEAD` (en-têtes OF)
- **Filtres** :
  - Statut : Planifiés, Suggérés, ou Fermes avec capacité restante
  - Exclure : OF clôturés, annulés

#### Nomenclatures
- **Source** : `MFGMAT` (composants OF), `ITMBOM` (nomenclatures article)
- **Profondeur** : Multi-niveaux (récursif)

#### Référentiel articles
- **Source** : `ITMMASTER` (fiches articles)
- **Attributs clés** :
  - Type : Achat vs Fabrication
  - Catégorie : SF*, PF*, autres (pour règle non-bloquant)

---

### 2.2 Règles métier

#### Règle 1 : Date de besoin production
```
Date besoin production = SHIDAT (date expédition) - 2 jours ouvrés
```

**Calcul jours ouvrés** :
- Exclure : Samedi, Dimanche
- Exclure : Jours fériés France (bibliothèque `holidays`)
- Exclure : Fermetures Aereco (onglet Excel paramétrable)

#### Règle 2 : Priorité d'allocation
```
Ordre de tri pour allocation séquentielle :
1. Date de besoin production croissante (plus urgent d'abord)
2. Priorité client (si champ priorité existe)
3. Ratio de faisabilité décroissant (tie-breaker)
```

#### Règle 3 : SF*/PF* non-bloquants
- **Articles catégorie SF*** (semi-finis) : non-bloquants pour le parent
- **Articles catégorie PF*** (produits finis intermédiaires) : non-bloquants pour le parent
- **Évaluation indépendante** :
  - OF parent évalué sur composants achetés uniquement
  - OF SF*/PF* évalué sur ses propres composants
- **Visibilité** : Afficher statut SF*/PF* associés sans bloquer le parent

#### Règle 4 : Appariement commande → OF
Pour les lignes "sur stock" sans OF dédié :

1. **Rechercher OF candidats** produisant le même article :
   - Statut : Planifié ou Suggéré
   - Ou Ferme avec capacité restante > 0

2. **Sélection** :
   - Date de fin OF ≤ Date de besoin commande
   - En cas de plusieurs candidats : choisir par (date fin croissante, capacité décroissante)

3. **Si aucun OF trouvé** : Alerte "Aucun approvisionnement"

#### Règle 5 : Double vue disponibilité
Pour chaque composant :
- **Vue instantanée** : ATP actuel ≥ besoin ?
- **Vue projetée** : ATP à date de besoin ≥ besoin ? (incluant réceptions attendues)

#### Règle 6 : Calcul ratio faisabilité
```
Ratio = Nombre de lignes composants couvertes à 100% / Nombre total de lignes composants

Composant "couvert" = ATP (instantané ou projeté selon vue) ≥ Quantité requise
```

**Deux ratios par OF** :
- Ratio instantané : basé sur ATP actuel
- Ratio projeté : basé sur ATP à date de besoin

---

### 2.3 Processus d'allocation

#### Étape 1 : Chargement données
1. Extraire commandes clients fermes dans l'horizon
2. Calculer date de besoin production pour chaque ligne
3. Calculer ATP par composant (stock + réceptions attendues - alloué)
4. Charger OF candidats avec capacité restante
5. Charger nomenclatures (multi-niveaux)

#### Étape 2 : Classification lignes
Pour chaque ligne de commande :
- **Contremarque** : Lien direct avec OF (champ MTOREF ou similaire)
- **Sur stock** : Pas de lien direct

#### Étape 3 : Allocation séquentielle
```
Pour chaque ligne de commande (triée par priorité) :
    Si contremarque :
        OF = OF lié
    Sinon :
        Si stock dispo >= besoin :
            Allouer stock
            OF = null
        Sinon :
            Chercher OF candidat
            Si OF trouvé :
                Allouer OF
            Sinon :
                Alerte "Aucun approvisionnement"
    
    Vérifier faisabilité OF (composants)
    Mettre à jour ATP (consommer stock alloué)
```

#### Étape 4 : Vérification faisabilité composants
Pour chaque OF identifié :
1. Explorer nomenclature (multi-niveaux)
2. Pour chaque composant acheté :
   - Vérifier ATP instantané
   - Vérifier ATP projeté à date de besoin
3. Calculer ratio faisabilité
4. Identifier composants manquants

---

## 3. SORTIES

### 3.1 Onglet 1 : Tableau de bord commandes (vue principale)

**Colonnes** :
| Colonne | Description |
|---------|-------------|
| N° Commande | SOHNUM |
| Ligne | SOPLIN |
| Client | BPCORD |
| Article | ITMREF |
| Désignation | ITMDES |
| Quantité commandée | QTY |
| Date expédition | SHIDAT |
| Date besoin prod. | Calculée (SHIDAT - 2 JO) |
| Type | Contremarque / Stock |
| OF associé | MFGNUM ou "Stock" ou "Aucun" |
| Statut global | 🟢 / 🟠 / 🔴 |
| Ratio instantané | % |
| Ratio projeté | % |
| Composants manquants | Nombre |
| SF*/PF* associés | 🟢 / 🟠 / 🔴 (indicateur) |

**Ordre de tri** : Date de besoin production croissante

**Feux tricolores** :
- 🟢 Vert : Ratio projeté = 100% (tous composants couverts à date)
- 🟠 Orange : Ratio instantané < 100% mais ratio projeté = 100% (couvert à date, pas maintenant)
- 🔴 Rouge : Ratio projeté < 100% (manque même à date)

### 3.2 Onglet 2 : Détail composants manquants

**Colonnes** :
| Colonne | Description |
|---------|-------------|
| N° Commande | SOHNUM |
| Ligne | SOPLIN |
| Composant | ITMREF |
| Désignation | ITMDES |
| Quantité requise | Calculée (qté OF × qté nomenclature) |
| ATP instantané | Quantité |
| ATP projeté | Quantité |
| Écart | Quantité manquante |
| Date prochaine réception | Si OA en cours |
| Source réception | N° OA ou N° OF amont |
| Type de manque | "Stock insuffisant" / "Aucun approvisionnement" / "OF non faisable" |

### 3.3 Onglet 3 : Vue OF (pour ateliers)

**Colonnes** :
| Colonne | Description |
|---------|-------------|
| N° OF | MFGNUM |
| Article produit | ITMREF |
| Quantité planifiée | EXTQTY |
| Date début | STRDAT |
| Date fin | ENDDAT |
| Statut | MFGSTA |
| Commandes servies | Liste SOHNUM |
| Ratio instantané | % |
| Ratio projeté | % |
| Composants manquants | Nombre |
| Action recommandée | "Lançable" / "Attendre réceptions" / "Relancer appro" |

### 3.4 Onglet 4 : Composants critiques

**Colonnes** :
| Colonne | Description |
|---------|-------------|
| Composant | ITMREF |
| Désignation | ITMDES |
| Stock physique | PHYSTO |
| Alloué ferme | CUMALLQTY |
| ATP actuel | Calculé |
| Réceptions attendues | Total OA + OF |
| Besoin total (horizon) | Somme besoins OF |
| Taux de couverture | % |
| Commandes impactées | Nombre |
| OF impactés | Liste MFGNUM |
| Action suggérée | "Relance fournisseur" / "Recherche substitution" / "OK" |

---

## 4. PARAMÈTRES UTILISATEUR

### 4.1 Onglet Paramètres (Excel)

| Paramètre | Valeur par défaut | Description |
|-----------|-------------------|-------------|
| Horizon (semaines) | 8 | Profondeur temporelle d'analyse |
| Décalage prod. (jours ouvrés) | 2 | SHIDAT - X = date besoin |
| Déduire allocations | Oui | Inclure allocations existantes dans ATP |
| Site | Aereco | Code site X3 |

### 4.2 Onglet Calendrier (fermetures Aereco)

| Date | Description |
|------|-------------|
| 2026-12-25 | Noël |
| 2026-01-01 | Jour de l'an |
| ... | ... |

**Format** : Date en colonne A, description en colonne B

---

## 5. CAS LIMITES ET EXCEPTIONS

### 5.1 OF sans nomenclature
- **Hypothèse** : N'existe pas dans l'environnement Aereco
- **Si détecté** : Alerte dans logs, OF considéré comme faisable à 100%

### 5.2 Composant fantôme
- **Définition** : Article type fabrication sans OF correspondant et sans stock
- **Traitement** : Marqué en rouge, alerte "Aucun approvisionnement identifié"

### 5.3 Boucle nomenclature
- **Détection** : Vérifier circularité au chargement
- **Traitement** : Alerte erreur données, arrêt traitement

### 5.4 Multiple OF pour même article
- **Sélection** : Date fin la plus proche ≤ date besoin
- **Égalité** : Capacité restante la plus grande

### 5.5 Ligne partiellement couverte
- **Stock < besoin** : Allouer stock disponible, chercher OF pour l'écart
- **Aucun OF** : Alerte, ratio basé sur couverture partielle

---

## 6. TECHNOLOGIE ET ARCHITECTURE

### 6.1 Stack technique
- **Interface** : Excel avec Python in Excel
- **Moteur** : Python 3.10+
- **Données** : CSV (POC) → ODBC (production)
- **Calendrier** : numpy (busday_offset) + holidays (fériés FR)

### 6.2 Architecture modulaire

```
ordonnanceur/
├── data/
│   ├── loader.py          # Chargement CSV/ODBC
│   └── validator.py       # Validation schéma
├── engine/
│   ├── atp_calculator.py  # Calcul ATP
│   ├── allocator.py       # Allocation séquentielle
│   ├── matcher.py         # Appariement commande→OF
│   ├── feasibility.py     # Faisabilité composants
│   └── calendar.py        # Jours ouvrés
├── output/
│   ├── dashboard.py       # Génération tableaux
│   └── exporter.py        # Export Excel
├── config/
│   └── settings.py        # Paramètres
└── main.py               # Point d'entrée
```

---

## 7. MÉTRIQUES ET KPIs

### 7.1 Indicateurs globaux
- Taux de commandes couvertes à 100% (vert)
- Taux de commandes couvertes à date mais pas maintenant (orange)
- Taux de commandes non couvrables (rouge)
- Nombre de composants critiques
- Taux de couverture moyen par composant

### 7.2 Indicateurs par OF
- Ratio faisabilité
- Nombre de composants manquants
- Date de couverture complète (si estimable)

---

## 8. ÉVOLUTIONS FUTURES (post-POC)

### Phase 2
- Intégration ODBC directe (sans CSV)
- Mode conservateur/optimiste (si besoin)
- Ordonnancement à capacité finie
- Optimisation automatique (suggestions réallocation)

### Phase 3
- Interface web (React)
- Temps réel (rafraîchissement automatique)
- Scénarios what-if
- Intégration Sage X3 Web Scheduling

---

## 9. GLOSSAIRE

| Terme | Définition |
|-------|------------|
| ATP | Available-to-Promise : stock disponible à promettre |
| OF | Ordre de Fabrication |
| SF* | Semi-fini (catégorie article) |
| PF* | Produit fini intermédiaire (catégorie article) |
| SHIDAT | Date d'expédition (Sage X3) |
| MFGSTA | Statut OF (Sage X3) |
| CBN | Calcul des Besoins Nets (MRP) |

---

## 10. VALIDATION

**Approbation requise** :
- [ ] Responsable Supply Chain
- [ ] Responsable Production
- [ ] Direction Industrielle

**Critères de succès POC** :
- [ ] Algorithmes corrects sur données fictives
- [ ] Temps de calcul < 30 secondes pour 1000 commandes
- [ ] Lisibilité tableau de bord validée par utilisateurs
- [ ] Cohérence avec données X3 réelles

---

**Document vivant** - Mise à jour selon retours implémentation
