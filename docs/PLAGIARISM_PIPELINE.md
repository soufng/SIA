# Pipeline de détection de plagiat — SIA

Document technique sur le sous-système de détection de plagiat, son
architecture, ses fondations mathématiques, ses limites connues et les
choix qui ont mené à l'état actuel.

Lecteurs visés : jury de soutenance, futur·e mainteneur·euse, équipe
métier voulant comprendre les choix.

---

## 1. Énoncé du problème

Soit un corpus de scénarios PDF déposés au fil du temps. Pour chaque
nouveau scénario, on veut :

1. **Détecter une copie verbatim ou paraphrasée** d'un scénario déjà
   présent dans le corpus.
2. **Refuser de signaler comme plagiat** deux scénarios qui ne font que
   partager le même *style* (format, langue, registre).
3. **Localiser** les passages copiés pour le rapport.
4. Tenir l'objectif sur un corpus **stylistiquement très homogène** :
   scénarios marocains contemporains avec mélange FR / arabe
   classique / darija, même mise en page (slug lines `INT. ... – JOUR`,
   dialogues en MAJUSCULES, didascalies courtes).

La contrainte (4) est ce qui rend le problème intéressant : un moteur
sémantique seul confond systématiquement (1) et la négation de (2).

---

## 2. Pourquoi un seul moteur sémantique ne suffit pas

L'approche initiale du projet reposait sur **e5-base multilingue +
Qdrant cosine + composite scoring avec heuristiques anti-faux-positifs**.

### Le problème mesuré en conditions réelles

Sur deux scénarios sans aucun rapport scénaristique (Omar/Zahra vs
Douae/Hiba — drame familial marocain vs drame de groupe à Marseille,
personnages disjoints, intrigues disjointes), e5-base produit
systématiquement des scores cosinus **≥ 0.60**, parfois > 0.75. La
raison :

- Mêmes embeddings de structure (`INT.`, `EXT.`, `JOUR`, `NUIT`)
- Même registre lexical (verbes émotionnels : *pleurer*, *regarder*,
  *trembler*…)
- Mêmes interjections darija (`مالك`, `والو`, `ألوا`) traitées
  comme des mots phatiques mais qui inflatent la similarité
- Même format de dialogue (PERSONNAGE en majuscule + ligne de texte)

E5-base est entraîné par contrastive learning sur de la similarité
sémantique générale — il a appris que "scène intime nocturne en
arabe / français" est proche d'une autre "scène intime nocturne en
arabe / français", indépendamment du contenu narratif.

### Le contre-exemple critique

Avec un seuil cosinus à 0.60, le système d'origine produisait **35 %
"MODÉRÉ"** comme verdict final sur des paires sans aucun mot consécutif
partagé. C'est exactement le scénario qui détruit la crédibilité d'un
outil de plagiat : un faux positif systémique sur la donnée pour
laquelle l'outil est conçu.

### Les heuristiques anti-faux-positifs n'ont pas suffi

On a empilé dans
[`composite_scoring.py`](../backend/utils/composite_scoring.py) des
règles successives :

- Strip des slug lines avant tokenisation
- Stopwords darija/arabes pour les interjections
- Cap du score si lexical < 0.15 et exact n-gramme < 0.05
- Cap si aucune entité nommée commune
- Filtrage en amont si `lexical + exact_overlap < 0.10`

Chaque règle réduit *un* type de faux positif sans s'attaquer à la
cause racine : **les embeddings mesurent la mauvaise chose pour
détecter du plagiat**.

---

## 3. Approche industrielle : fingerprinting lexical

Turnitin, Copyleaks, Plagscan, JPlag (code) reposent depuis ~20 ans
sur la même idée : **on ne détecte pas une copie en mesurant le sens
des phrases, on la détecte en mesurant le partage de séquences exactes
de mots** (ou de tokens normalisés).

C'est l'**algorithme de Broder** [1] sur la *resemblance* de documents,
utilisé chez AltaVista en 1997 pour la déduplication du Web et adapté
ensuite à la détection de plagiat.

### 3.1 Shingles

Étant donné un document tokenisé `T = (t₁, t₂, …, tₙ)`, on calcule
l'ensemble de ses *k*-shingles :

```
S_k(T) = { (tᵢ, tᵢ₊₁, …, tᵢ₊ₖ₋₁)  |  1 ≤ i ≤ n − k + 1 }
```

Dans SIA, **k = 5** ([`SHINGLE_SIZE`](../backend/services/minhash_service.py)).
C'est un compromis :

- k = 3 : trop court, des trigrammes communs ("il se lève", "elle
  ferme les yeux") créent du bruit
- k = 7 : trop long, manque les copies paraphrasées avec 2-3 mots
  intermédiaires modifiés
- k = 5 : seuil empirique standard dans la littérature pour le texte
  narratif

Les tokens passent d'abord par
[`normalize_tokens`](../backend/utils/composite_scoring.py) :
casefold + suppression des diacritiques + retrait des stopwords
(slug lines, boilerplate, interjections darija) + drop des tokens
< 3 caractères sauf arabe.

### 3.2 Mesure de similarité : Jaccard

Pour deux documents A et B :

```
J(A, B) = |S(A) ∩ S(B)| / |S(A) ∪ S(B)|
```

Propriétés clés pour la détection de plagiat :

- `J = 0` ⇔ aucun *k*-shingle commun ⇒ **aucune séquence de k mots
  consécutifs partagée**
- `J = 1` ⇔ shingles identiques (copie verbatim modulo
  réordonnancement)
- Robuste aux insertions/suppressions locales (plus que cosinus sur
  embeddings)
- **Mesure ce qu'on veut mesurer** : du partage textuel

### 3.3 Pourquoi MinHash et pas le Jaccard direct

Calculer Jaccard sur tous les shingles d'un document est rapide en
soi, mais comparer un nouveau document à `N` documents indexés coûte
`O(N × |S|)` en intersections de sets.

**MinHash** [1] approxime Jaccard avec une signature de taille fixe.
Soit `h₁, …, h_p` p fonctions de hash (`p = 128` dans SIA). On définit :

```
mh_j(D) = min { h_j(s)  |  s ∈ S(D) }
```

La signature MinHash de `D` est `(mh₁(D), …, mh_p(D))`. Théorème
fondamental de Broder :

```
Pr[ mh_j(A) = mh_j(B) ] = J(A, B)
```

Donc estimer Jaccard revient à compter la fraction d'index `j` où les
signatures coïncident. Erreur typique avec p = 128 : ≈ 1/√p ≈ 8.8 %.

### 3.4 LSH pour la recherche sub-linéaire

Comparer la signature d'un nouveau document à `N` signatures coûte
`O(N × p)`. Pour `N` grand, on utilise **Locality Sensitive Hashing**
[2] : on découpe la signature en `b` bandes de `r` lignes (`b × r = p`)
et on hashe chaque bande. Deux signatures finissent dans le même
bucket d'au moins une bande si elles ont une probabilité > seuil
d'être similaires.

`datasketch.MinHashLSH(threshold=0.05, num_perm=128)` calcule
automatiquement `b` et `r` pour cette cible. Le seuil LSH est
volontairement **lâche (0.05)** : on accepte des candidats de très bas
Jaccard estimé, puis on **recalcule le Jaccard exact** sur les
candidats retournés pour ne garder que ceux ≥ 0.10. Ça maximise le
recall sur les paraphrases partielles tout en gardant la précision.

---

## 4. Mise en œuvre dans SIA

### 4.1 Composants

| Fichier | Rôle |
|---|---|
| [`minhash_service.py`](../backend/services/minhash_service.py) | `MinHashIndex` singleton thread-safe + bootstrap depuis Qdrant |
| [`minhash_plagiarism_service.py`](../backend/services/minhash_plagiarism_service.py) | Itère sur les chunks, query l'index, formate les matches |
| [`vector_service.py`](../backend/services/vector_service.py) | `_mirror_to_minhash` à chaque upsert Qdrant |
| [`plagiarism_pipeline.py`](../backend/pipelines/plagiarism_pipeline.py) | Orchestration des 3 moteurs + verdict combiné |
| [`composite_scoring.py`](../backend/utils/composite_scoring.py) | Tokens normalisés, slug-line stripping, composite scoring résiduel |

### 4.2 Cycle de vie de l'index

```
Démarrage uvicorn (par worker)
   ↓
lifespan() → bootstrap_from_qdrant()
   ↓
   Scroll complet de la collection Qdrant
   ↓
   Pour chaque point : build_signature → LSH.insert
   ↓
   index.mark_bootstrapped()

Analyse d'un nouveau PDF
   ↓
MinHashPlagiarismService.analyze_chunks
   ↓
_ensure_bootstrapped → re-scroll incrémental (ajoute uniquement les
                       nouvelles clés)
   ↓
Pour chaque chunk de B : LSH.query(signature) → candidats
                         → Jaccard exact → match si ≥ 0.10

Upsert dans Qdrant (en fin d'analyse)
   ↓
VectorService._mirror_to_minhash → LSH.insert (worker courant)
   ↓
Les autres workers verront les nouveaux chunks à leur prochaine sync.
```

### 4.3 Multi-worker — le piège évité

Uvicorn lance `--workers 2`. Chaque worker est un **processus** avec
son propre espace mémoire — le singleton `MinHashIndex` est donc
**par-worker**, pas global. Sans précaution, un chunk upserté via le
worker A est invisible au worker B → faux négatif aléatoire selon
quel worker reçoit l'analyse.

Solutions envisagées et rejetées :
- **`--workers 1`** : perd la concurrence
- **Persister l'index sur disque** : I/O à chaque upsert
- **Redis / index partagé** : surdimensionné pour le besoin

Solution retenue : **resync incrémental à chaque analyse** dans
`_ensure_bootstrapped`. Coût : un scroll Qdrant (sub-seconde sur le
volume actuel) + N appels `add_chunk` no-ops pour les clés déjà
présentes. Bénéfice : zéro divergence entre workers.

---

## 5. Verdict combiné

### 5.1 Score affiché

Pseudo-code (simplifié) dans `_merge_plagiarism_results` :

```python
if local_exact_duplicate:           # hash fichier ou texte identique
    final_score = 1.0
elif minhash_best > 0:               # MinHash trouve quelque chose
    final_score = max(minhash_best, 0.7 * best_composite)
else:                                # ni MinHash ni doublon : fallback
    final_score = max(best_composite, local_score)
```

L'idée : si MinHash a un signal, c'est lui qui domine. Le composite
sémantique ne peut "tirer" le score que sur 70 % de sa valeur, et
**jamais** au-dessus de MinHash.

### 5.2 Risk bucket

| MinHash Jaccard | Bucket |
|---|---|
| ≥ 0.40 | very_high |
| ≥ 0.20 | high |
| ≥ 0.10 | medium |
| < 0.10 | low |

Calibration : on a empiriquement constaté qu'un Jaccard ≥ 0.25 sur des
chunks de scénarios indique systématiquement une copie volontaire
(verbatim ou paraphrasée par changement de noms). Les vrais positifs
"légers" tombent entre 0.10 et 0.20.

### 5.3 Filtrage des matches affichés

Pour les **matches sémantiques** (issus de e5 seul) :

```
si MinHash a tourné sans erreur ET sans doublon exact :
    pour chaque match sémantique :
        s'il n'a pas de jumeau MinHash pour le même (chunk, source) → drop
```

Conséquence : quand MinHash dit 0 %, le rapport n'affiche **rien** —
pas de bandeau "ressemblance à 35 %", pas de tableau de matches
sémantiques fantômes. Le verdict du haut ("pas un plagiat") est
cohérent avec ce qui est listé en bas (rien).

C'est l'expression dans le code de la philosophie "la crédibilité
demande des arbitrages stricts" : on **préfère ne rien dire** que
laisser un lecteur faire un faux raisonnement à partir d'un score
sémantique élevé.

---

## 6. Validation empirique

Quatre cas couvrent les coins du problème :

### Cas 1 — Paraphrase volontaire (vrai positif)

**Villa des Vents A** et **La Maison du Vent B** : même intrigue,
mêmes structures de scènes, mêmes phrases-clés, **noms changés**
(Yassine/Leila ↔ Nadir/Myriam) et locations renommées.

| Métrique | Valeur |
|---|---|
| MinHash global | **76 %** |
| Composite sémantique | 80 % |
| Verdict | 🔴 Plagiat textuel confirmé |
| Best match | 80 % sur l'en-tête (quasi-verbatim) |

### Cas 2 — Drames sans rapport (vrai négatif)

**Omar/Zahra** (drame familial Maroc, décès de la mère) vs
**Douae/Hiba** (drame de groupe Marseille, grossesse). Aucun
personnage commun, aucune scène commune, aucune phrase partagée.

| Métrique | Valeur |
|---|---|
| MinHash global | **0 %** |
| Composite sémantique | 35 % |
| Verdict | 🟢 Pas un plagiat |
| Matches affichés | **0** (filtrés) |

### Cas 3 — Document composite (vrai positif partiel)

Document fabriqué en collant deux passages d'un scénario tiers
("les 4 saisons") dans un scénario par ailleurs original.

| Métrique | Valeur |
|---|---|
| MinHash global | **25 %** |
| Composite sémantique | 40 % |
| Verdict | 🔴 Plagiat textuel confirmé |
| Best match | 57 % (passage du boucher quasi-verbatim) |
| Second match | 17 % (passage Damia avec légère réécriture) |

Le score global de 25 % est important : il **ne sur-réagit pas** —
seule une partie est copiée, le score le reflète.

### Cas 4 — Doublon exact (vrai positif trivial)

Upload du même PDF deux fois. Géré par
[`LocalSimilarityService`](../backend/services/local_similarity_service.py)
via hash SHA-256 du fichier puis hash du texte nettoyé. Verdict :
100 % "Doublon exact", trump tout.

---

## 7. Limites connues

À mentionner honnêtement dans toute présentation.

### 7.1 Paraphrase profonde par LLM

Un plagiat passé à GPT-4 / Claude avec consigne "réécris dans un
style différent, garde l'intrigue" peut produire un texte avec **<
5 % de shingles partagés** tout en restant un plagiat narratif. MinHash
ne le détectera pas. Le seul signal résiduel sera la similarité
sémantique e5 — qui, sans confirmation textuelle, ne suffit pas à
qualifier le plagiat dans notre architecture actuelle.

Mitigation future possible : passe LLM-as-judge en bout de chaîne sur
les paires à fort cosinus mais faible MinHash. Coûteux, à activer
sélectivement.

### 7.2 Traduction inter-langue

Un scénario en arabe traduit en français (ou inversement) partage
zéro shingle. MinHash invisible. e5-base multilingue **pourrait**
détecter (les embeddings sont alignés cross-langue), mais le signal
serait noyé dans le bruit stylistique.

Mitigation future : embeddings alignés type LaBSE en pass complémentaire,
réservés aux paires explicitement marquées comme à risque
cross-lingue.

### 7.3 Documents très courts

Si un chunk fait moins de `SHINGLE_SIZE` tokens informatifs après
filtrage, `make_shingles` retourne ∅ et le chunk n'est pas indexé.
Concrètement : un scénario de moins d'une page de texte utile passe
sous le radar. Acceptable dans le contexte CCM (scénarios longs).

### 7.4 Hyper-sensibilité aux tokens "rares" partagés

Si deux scénarios partagent un terme rare (nom de lieu réel, jargon
technique), le Jaccard peut être gonflé par quelques shingles autour
de ce terme. Le seuil de 10 % protège dans la pratique, mais sur des
chunks très courts un seul shingle "rare" partagé peut faire monter
le score local. Ce n'est pas une faiblesse critique : un humain
relisant le passage voit immédiatement qu'il ne s'agit pas d'un
plagiat.

### 7.5 Index volatile

L'index MinHash vit en mémoire. Un redémarrage du backend implique un
re-bootstrap depuis Qdrant — typiquement quelques secondes pour des
milliers de chunks. Acceptable. Le scénario "Qdrant down au boot" laisse
l'index vide mais ne casse pas la pipeline (fallback sémantique seul).

---

## 8. Choix architecturaux explicites

- **MinHash plutôt qu'embeddings spécialisés plagiat** (e.g. SimCSE,
  BGE) : la mesure Jaccard est *interprétable*, *déterministe*, et
  *expliquable* à un comité éditorial. Un score de 0.30 = 30 % de
  shingles partagés, pas une distance dans un espace latent.
- **Index in-process plutôt qu'un service externe** : la dépendance
  externe (Qdrant) reste unique. Pas de Redis pour MinHash, pas de
  base sidecar.
- **Resync incrémental plutôt que pub/sub** : robuste, sans état
  partagé, indépendant du nombre de workers, gratuit en complexité.
- **Filtrage des matches sémantiques sans preuve MinHash** : choix
  éditorial fort. On fait passer la lisibilité du verdict avant
  l'exhaustivité de l'affichage. Décision révisable si le retour
  utilisateur demande plus de transparence.

---

## 9. Références

[1] **A.Z. Broder.** *On the resemblance and containment of documents.*
SEQUENCES 1997. — Le papier fondateur du shingling + MinHash, encore
la référence.

[2] **P. Indyk, R. Motwani.** *Approximate nearest neighbors: towards
removing the curse of dimensionality.* STOC 1998. — Théorie LSH.

[3] **datasketch** documentation —
https://ekzhu.com/datasketch/ — Implémentation Python utilisée
dans SIA. Vendor-stable, maintenue, MIT.

[4] **L. Wang, N. Yang et al.** *Text Embeddings by Weakly-Supervised
Contrastive Pre-training.* arXiv 2022. — Architecture des embeddings
e5, modèle de notre couche sémantique.

[5] **Stanford CS246 — Mining Massive Datasets**, chapitre 3 *Finding
Similar Items.* — Excellente référence pédagogique sur shingles +
MinHash + LSH.

---

## 10. Reproduire la validation localement

```bash
# 1. Reset complet
.\scripts\reset_all.ps1

# 2. Uploader la source (cas 1)
# UI : Accueil → drag Villa A → Lancer l'analyse

# 3. Uploader le document à tester
# UI : Accueil → drag Villa B → Lancer l'analyse

# 4. Lire le verdict
# Attendu : MinHash ≥ 70 %, verdict 🔴 "Plagiat textuel confirmé"

# 5. Inspecter l'index MinHash en live
docker exec sia_backend python -c "
from backend.services.minhash_service import MinHashIndex
print('size:', MinHashIndex.get().size())
"
```

Pour itérer sur les seuils :

| Knob | Réglage actuel | Effet d'une augmentation |
|---|---|---|
| `SHINGLE_SIZE` | 5 | Moins de matches, plus précis |
| `MIN_REPORT_JACCARD` | 0.05 | Filtrage plus strict des candidats LSH |
| `DEFAULT_MIN_JACCARD` | 0.10 | Seuil d'affichage plus exigeant |
| `_risk_from_minhash` thresholds | 0.10 / 0.20 / 0.40 | Décale les buckets de risque |

Toute modification de `SHINGLE_SIZE` invalide les signatures
existantes — il faut un re-bootstrap (redémarrage du backend suffit).
