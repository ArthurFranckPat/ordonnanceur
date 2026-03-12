# Revalidation besoin vs POC prod

## Conclusion courte

Le vrai point de depart doit etre `moteur_ordonnancement.py`, car c'est lui qui porte le schema reel des CSV de production et les arbitrages metier effectivement implementes.

La spec V3 reste utile comme cible fonctionnelle, mais elle n'est pas equivalente au comportement du POC prod actuel. Toute refonte doit donc partir de la comparaison ci-dessous.

## Ce qui est aligne

- Date de besoin production = date expedition - 2 jours ouvres: oui (`moteur_ordonnancement.py:166`)
- Exclusion weekends + feries + fermetures Aereco: oui, meme si les fermetures ne sont pas encore alimentees (`moteur_ordonnancement.py:50`, `moteur_ordonnancement.py:53`)
- Distinction contremarque / sur stock: oui (`moteur_ordonnancement.py:168`)
- Allocation stock en premier pour les lignes sur stock: oui (`moteur_ordonnancement.py:197`)
- Recherche d'un OF pour couvrir le reliquat: oui (`moteur_ordonnancement.py:221`)
- Vue instantanee et projetee des composants: oui (`moteur_ordonnancement.py:304`)
- SF*/PF* non bloquants pour le parent avec evaluation recursive des enfants: oui (`moteur_ordonnancement.py:184`, `moteur_ordonnancement.py:337`)
- Sorties metier riches: commandes, detail manquants, plan de charge, composants critiques: oui (`moteur_ordonnancement.py:718`, `moteur_ordonnancement.py:757`, `moteur_ordonnancement.py:574`, `moteur_ordonnancement.py:781`)

## Ecarts fonctionnels majeurs

### 1. Priorite d'allocation

Spec V3:
- tri par date de besoin production croissante
- puis priorite client
- puis ratio de faisabilite decroissant

POC prod:
- les commandes sont triees par `shidat` seulement (`moteur_ordonnancement.py:171`)
- aucune priorite client n'est prise en compte
- l'allocation sequentielle des OF est triee par ratio projete decroissant puis date de fin OF (`moteur_ordonnancement.py:381`)

Impact:
- l'ordre reel de consommation des composants n'est pas celui defini par la spec V3

### 2. Regle de selection commande -> OF

Spec V3:
- OF candidats: planifie, suggere, ou ferme avec capacite restante
- date fin OF <= date besoin commande
- tie-break: date fin croissante puis capacite decroissante

POC prod:
- cherche dans tous les OF actifs avec `qte_restante > 0` (`moteur_ordonnancement.py:223`)
- exclut les OF deja attribues via un `set` global (`moteur_ordonnancement.py:226`)
- choisit l'OF avec `abs(enddat - date_besoin)` minimale (`moteur_ordonnancement.py:239`)
- ne filtre pas sur `enddat <= date_besoin`
- ne gere pas explicitement une capacite restante multi-commandes autre que l'exclusivite d'affectation

Impact:
- un OF trop tardif peut etre choisi
- la logique actuelle ressemble a une affectation 1 commande <-> 1 OF, pas a une logique de capacite restante partagee

### 3. Perimetre d'evaluation des OF

Spec V3:
- tous les OF identifies pour les commandes doivent etre verifies

POC prod:
- les OF non affermis sont evalues dans `alloc_seq_composants` (`moteur_ordonnancement.py:816`, `moteur_ordonnancement.py:822`)
- les OF fermes sont evalues plus tard dans `assembler`, un par un, hors allocation sequentielle globale (`moteur_ordonnancement.py:643`)

Impact:
- les OF fermes ne passent pas dans le meme moteur d'allocation sequentielle composants que les OF non affermis

### 4. Allocation sequentielle composants

Spec V3:
- allocation sequentielle par priorite de commande
- consommation ATP au fil du traitement commande par commande

POC prod:
- la sequence se fait au niveau liste d'OF, pas au niveau ligne de commande (`moteur_ordonnancement.py:375`)
- l'ordre des OF est pilote par ratio projete puis date OF, pas par priorite commande (`moteur_ordonnancement.py:381`)
- la consommation sequentielle ne decompte que les composants bloquants et les OF enfants decouverts (`moteur_ordonnancement.py:388`)

Impact:
- la regle metier centrale "priorite par besoin client" n'est pas strictement respectee

### 5. Calcul du feu orange / rouge

Spec V3:
- vert: ratio projete = 100% et ratio instant = 100%
- orange: ratio instant < 100% et ratio projete = 100%
- rouge: ratio projete < 100%

POC prod:
- vert si ratio projete = 100 et ratio instant = 100 (`moteur_ordonnancement.py:365`)
- orange si ratio projete = 100 et ratio instant < 100, mais aussi si ratio projete > 0 (`moteur_ordonnancement.py:365`, `moteur_ordonnancement.py:366`)

Impact:
- un OF partiellement couvrable a date passe orange dans le POC, alors que la spec V3 le veut rouge

### 6. Ratio de faisabilite

Spec V3:
- ratio = lignes couvertes / lignes totales composants
- parent evalue sur composants achetes uniquement, SF/PF visibles a part

POC prod:
- ratio instant/projete est calcule sur toutes les lignes composants (`moteur_ordonnancement.py:364`)
- la notion de `lancable` est calculee separement sur les seuls composants bloquants (`moteur_ordonnancement.py:368`)

Impact:
- il y a deux verites en parallele: un ratio global et une decision de lancabilite basee sur les seuls bloquants
- la spec V3 est plus proche de la logique `lancable` que du ratio tel qu'affiche aujourd'hui

### 7. Chargement capacitaire

Spec V3:
- la capacite n'etait pas centrale dans le coeur V3; c'etait plutot une vue atelier complementaire

POC prod:
- la capacite est une vraie dimension du resultat, avec backscheduling, surcharge par semaine et feu capacite (`moteur_ordonnancement.py:414`, `moteur_ordonnancement.py:660`)

Impact:
- le besoin reel semble plus large que la spec V3: on n'est pas seulement sur "faisabilite matiere", mais sur "faisabilite matiere + signal capacitaire"

### 8. Parametres fonctionnels encore actifs dans le POC

POC prod:
- `mode_injection = conservateur` est encore present (`moteur_ordonnancement.py:19`)
- `perimetre_chargement` et `capacite_heures_semaine` sont des choix metier reels (`moteur_ordonnancement.py:22`, `moteur_ordonnancement.py:23`)

Impact:
- la spec V3 avait simplifie certains points qui existent encore dans le comportement reel

## Direction recommandee

## Source de verite

- Source de verite technique immediate: `moteur_ordonnancement.py`
- Source de verite fonctionnelle cible: spec V3, mais a corriger pour refleter les arbitrages reellement voulus

## Decision de travail recommandee

1. Geler `moteur_ordonnancement.py` comme reference comportementale
2. Rejouer le besoin metier point par point a partir de ce fichier
3. Arbitrer explicitement chaque ecart ci-dessus en disant soit:
   - "on garde le comportement actuel du POC prod"
   - "on aligne sur la spec V3"
4. Ensuite seulement refactorer

## Arbitrages a trancher en priorite

1. La priorite sequentielle doit-elle etre portee par la commande ou par l'OF ?
2. Un OF dont la date de fin est apres le besoin commande peut-il etre propose quand meme ?
3. Le feu orange doit-il couvrir les cas partiellement faisables a date, ou seulement les cas 100% faisables a date mais pas maintenant ?
4. Le ratio affiche doit-il etre calcule sur tous les composants, ou seulement les bloquants ?
5. Les OF fermes doivent-ils entrer dans la meme allocation sequentielle composants que les OF non affermis ?
6. La charge capacitaire est-elle dans le coeur du besoin, ou seulement un indicateur annexe ?

## Recommendation concrete

La bonne suite n'est pas d'etendre le moteur modulaire actuel. La bonne suite est de repartir de `moteur_ordonnancement.py`, d'en extraire les regles stables, puis de le modulariser sans changer le comportement valide tant que les arbitrages ci-dessus ne sont pas clos.
