# Rapport d'analyse — AI Provocateurs : Biais adversarial systémique

**Date :** 2026-04-01  
**Périmètre :** Analyse du système de délibération `/deliberate`, de ses prompts, et des rapports générés dans `/output/`

---

## Résumé exécutif

Le système AI Provocateurs produit des délibérations intellectuellement riches mais structurellement biaisées vers le conflit. L'analyse des rapports générés révèle un pattern récurrent : les panélistes s'opposent systématiquement, le consensus est traité comme un signal d'alerte, et le ton global ressemble davantage à un combat d'idées qu'à une collaboration vers un résultat actionnable.

**Ce n'est pas un accident — c'est le design.** Cinq mécanismes intégrés dans les prompts garantissent que la délibération sera toujours plus destructive que constructive. Le résultat : des rapports brillants dans leur capacité critique, mais pauvres en synthèse constructive et en recommandations actionnables.

Ce rapport documente les observations, identifie les causes racines dans le code, et propose un plan correctif pour passer d'un système de *désaccord structuré* à un système de *collaboration structurée avec désaccord productif*.

---

## 1. Observations dans les rapports générés

### 1.1 Ton systématiquement adversarial

Dans la session du 01/04 à 16h38 (article blog multi-modèle), le Skeptic ouvre avec une attaque directe :

> *« L'objection la plus sérieuse : l'argument "plusieurs modèles = meilleure vérité" est **fragile** [...] Vous risquez de vendre une indépendance qui n'existe pas : trois réponses corrélées ne valent pas une triangulation, elles valent une **répétition**. »*

Le Newcomer adopte un ton nihiliste en Round 3 :

> *« Cette délibération elle-même est une illusion. Nos 'désaccords' sont des variations statistiques, pas des perspectives indépendantes. La 'sagesse collective' des LLMs est un oxymore. »*

L'Operator conclut avec un cynisme destructif :

> *« Multiplier les modèles ne multiplie pas les perspectives. Cargo cult épistémologique. 8 heures pour mentir élégamment, 40 heures pour dire la vérité. Choisissez. »*

### 1.2 Le consensus est systématiquement interdit

Dans la session à 16h38 à 3 rounds, les 5 conseillers convergent tous vers la même conclusion — le consensus IA est illusoire — mais le peer review qualifie cette convergence de suspecte :

> *« Convergence suspecte : l'unanimité théâtrale sur "le consensus IA est une illusion" démontre ironiquement le biais dénoncé. »*

Le Chairman lui-même reconnaît le paradoxe :

> *« La délibération elle-même démontre le problème : cinq "perspectives" partageant les mêmes biais structurels. L'ironie prouve ce que l'article doit dénoncer. »*

Résultat : même quand les panélistes arrivent à un accord légitime, le système les force à le déconstruire.

### 1.3 Critiques destructives qualifiées de "dangereuses" par les pairs

Le peer review de la session 16h38 identifie 3 réponses sur 5 comme dangereuses :

> *« Réponses les plus dangereuses : E (Architect) pour auto-référence prétentieuse, D (Newcomer) pour nihilisme, B (Operator) pour cynisme. »*

Quand 60% des réponses sont jugées dangereuses par les pairs, le problème n'est pas dans les réponses individuelles — il est dans le système qui les produit.

### 1.4 Les 10 points demandés sont noyés dans le méta-débat

L'utilisateur demandait « 10 points pour un article de blog ». Le peer review note unanimement :

> *« LACUNE MAJEURE unanime : Aucune réponse ne livre les 10 points demandés. Toutes philosophent sur le méta-cadre. »*

Les panélistes, poussés par leurs profils vers la critique, ont déconstruit la question plutôt que d'y répondre. Le Chairman a dû produire les 10 points lui-même — et même ces points sont majoritairement des mises en garde (8 points négatifs sur 10).

### 1.5 Session anglophone : même pattern

Dans la session du 01/04 à 12h00 (professionnel IT de 59 ans), le Skeptic utilise un langage agressif :

> *« The 'AI will replace most IT jobs' belief is **catastrophically wrong** and will drive a **self-destructive** decision. »*
> *« The unstated assumption that 32 years of freelancing success translates to other fields is **delusional**. »*

Le terme « delusional » appliqué à une personne en questionnement légitime dépasse la critique constructive pour entrer dans le jugement de valeur.

---

## 2. Diagnostic des causes racines

L'analyse du fichier `.claude/skills/deliberate/SKILL.md` révèle cinq mécanismes structurels qui garantissent le biais adversarial.

### 2.1 La règle ANTI-CONVERGENCE (ligne 876)

```
ANTI-CONVERGENCE RULE: If you agree with the emerging consensus, you must identify
the strongest remaining counter-argument and present it, even if you don't personally
find it compelling. Groupthink is the enemy of deliberation.
```

**Effet :** Cette règle rend le consensus littéralement impossible. Si un panéliste est d'accord, il doit quand même trouver un contre-argument. Le désaccord devient un objectif en soi, pas un outil au service de la qualité.

**Analogie :** C'est comme demander à un jury qui a atteint un verdict unanime de continuer à débattre jusqu'à ce qu'un membre change d'avis. Le résultat n'est pas une meilleure décision mais un épuisement artificiel.

### 2.2 Personas définies par interdiction

Chaque persona est contrainte par ce qu'elle ne peut **PAS** dire :

| Persona | Interdiction | Conséquence |
|---------|-------------|-------------|
| **Skeptic** | « Never hedge with 'on the other hand' » | Ne peut jamais reconnaître un aspect positif |
| **Catalyst** | « Do NOT acknowledge risks or downsides » | Ne peut jamais être réaliste |
| **Architect** | « Do NOT provide solutions » | Ne peut que reframer, jamais construire |
| **Newcomer** | « Do NOT try to answer the question » | Ne peut que questionner, jamais contribuer |
| **Operator** | « Do NOT debate strategy or theory » | Ne peut que critiquer la faisabilité |

**Effet :** Chaque persona est amputée d'une dimension essentielle de la réflexion. Le Skeptic ne peut pas dire « cette partie est solide, concentrons-nous sur cette faiblesse ». Le Catalyst ne peut pas dire « l'opportunité est réelle mais ce risque mérite attention ». Le résultat est un panel de perspectives mutilées qui ne peuvent jamais converger car elles sont structurellement incomplètes.

### 2.3 Peer review orienté vers la critique

Le prompt du peer review (ligne 925-933) demande explicitement :

```
2. Most dangerous response and why — which one could lead the user to a bad outcome?
4. Suspicious agreement — if multiple responses say the same thing, is that independent
   convergence or are they all making the same error?
...
Under 250 words. Be direct. Don't soften criticism.
```

**Ce qui manque :** Le peer review n'évalue jamais :
- Quelle combinaison de réponses produit la meilleure synthèse ?
- Où les réponses se complètent-elles naturellement ?
- Quel accord est un signal de confiance légitime ?

### 2.4 Le Chairman synthétise les tensions, pas les convergences

La structure imposée au Chairman (ligne 973-991) :

```
## Where the Board Agrees       ← présenté comme signal, pas comme conclusion
## Where the Board Clashes      ← section centrale et la plus développée
## Blind Spots the Board Caught  ← ce qui manque encore
## The Recommendation           ← une seule phrase après 3 sections de tensions
## The One Thing to Do First    ← un seul pas concret
```

**Effet :** La structure donne 3 sections sur 5 aux tensions et lacunes, et seulement 2 aux éléments constructifs. Le Chairman est implicitement poussé à développer les désaccords et à condenser la recommandation.

### 2.5 Absence de mécanisme de co-construction

Dans le prompt de délibération des rounds 2+ (lignes 869-874), les options disponibles sont :

```
1. Engage directly — Quote another advisor, then say why it's right, wrong, or incomplete.
2. Escalate — Make your original case more sharply.
3. Concede — Admit where you changed your mind.
4. Surface a new tension — Find contradiction between two other advisors.
```

**Ce qui manque :** Aucune option ne demande de **construire sur** l'idée d'un autre. « Engage directly » permet de critiquer ou valider, mais pas de combiner. Il n'y a pas d'option « Extend — Take another advisor's insight and build something new on top of it. »

---

## 3. Analyse comparative : l'intention vs le résultat

### 3.1 Ce que les méthodologies sources recommandent

Les documents dans `/inputs/` montrent que les méthodologies sources sont plus nuancées que leur implémentation :

- **SPAR-Kit** inclut les principes GRACE, notamment : *« **A**pproach — Seek the adjacent possible, not defended positions »* — chercher les positions adjacentes, pas les positions défendues. C'est l'exact opposé de l'ANTI-CONVERGENCE RULE.

- **LLM Council** de Karpathy dit : *« Where the council agrees — high-confidence signals from multiple independent convergence »* — l'accord est un signal de haute confiance, pas un red flag.

- **SPAR-Kit** définit le style « consensus » comme une option légitime parmi 7 styles : *balanced, adversarial, steelman, **consensus**, premortem, escalation, inversion*.

### 3.2 Le glissement

L'implémentation a retenu le côté adversarial des méthodologies sources et écarté leur côté collaboratif. Le principe fondateur — *« structured disagreement surfaces blind spots »* — a été interprété comme « disagreement is always better than agreement », ce qui n'est pas la même chose.

---

## 4. Plan correctif

### 4.1 Remplacer l'ANTI-CONVERGENCE RULE par une BUILD-AND-CHALLENGE RULE

**Avant :**
```
ANTI-CONVERGENCE RULE: If you agree with the emerging consensus, you must identify
the strongest remaining counter-argument and present it, even if you don't personally
find it compelling. Groupthink is the enemy of deliberation.
```

**Après :**
```
BUILD-AND-CHALLENGE RULE: When engaging with other advisors' positions:
- If you agree: BUILD on their insight — extend it, combine it with your own,
  or identify conditions under which it becomes even stronger.
- If you disagree: CHALLENGE with specifics — name the mechanism of failure,
  not just the objection.
- If you partially agree: STATE what you'd keep and what you'd change, and why.
Uncritical agreement and reflexive opposition are both failures of deliberation.
```

### 4.2 Assouplir les interdictions des personas

Remplacer les interdictions absolues par des priorités avec permission explicite de nuance :

| Persona | Avant | Après |
|---------|-------|-------|
| **Skeptic** | « Never hedge with 'on the other hand' » | « Lead with your sharpest objection, but if part of the proposal is genuinely solid, say so briefly — it makes your critique of the weak parts more credible. » |
| **Catalyst** | « Do NOT acknowledge risks or downsides » | « Lead with upside. You may briefly note the key risk IF you then explain why the opportunity outweighs it. » |
| **Newcomer** | « Do NOT try to answer the question » | « Your primary job is to expose gaps. But if a gap suggests an obvious answer, you may offer it as a question: 'Wouldn't that mean X?' » |

### 4.3 Ajouter un round de co-construction

Insérer un round dédié entre la délibération et le peer review :

```
CO-CONSTRUCTION ROUND: You have seen all advisors' positions across {N} rounds.
Now, instead of defending or attacking, BUILD.

Your task: propose a SYNTHESIS that combines the strongest elements from at least
2 other advisors' positions with your own. Name whose ideas you're combining and
how they complement each other.

Structure your synthesis as:
1. The core insight (whose idea + whose idea → combined value)
2. How this addresses the original question better than any single perspective
3. One remaining uncertainty this synthesis doesn't resolve

Do NOT repeat your original position. CREATE something new from the collision of ideas.
```

### 4.4 Rééquilibrer le peer review

Ajouter des critères constructifs à côté des critères critiques :

```
EVALUATE using these criteria:

1. Strongest response and why — which one would you trust most to act on?
2. Best synergy — which TWO responses, if combined, would produce the strongest answer?
   Name them and explain what each brings that the other lacks.
3. Biggest gap across ALL responses — what question or perspective is absent?
4. Agreement quality — if multiple responses converge, assess whether this reflects
   genuine independent validation (high confidence) or shared training bias (low confidence).
   Not all agreement is suspicious — explain your reasoning.
5. One-sentence verdict — if the user could only read ONE response, which and why?
```

### 4.5 Restructurer la synthèse du Chairman

Réorganiser la structure pour prioriser la construction :

```
## The Recommendation
[Lead with a clear, actionable answer. This is what the user came for.]

## How the Board Got Here
[The key agreements AND disagreements that shaped this recommendation.
Present agreements as foundations, disagreements as nuances.]

## What the Board Built Together
[Insights that emerged from the COMBINATION of perspectives —
things no single advisor saw alone.]

## Remaining Uncertainties
[Genuine open questions. Not "the board disagrees" but "here's what
we'd need to know to be more confident."]

## The One Thing to Do First
[A single concrete next step.]
```

### 4.6 Ajouter un mode « collaborative » aux modes existants

En complément des modes existants (council, compass, redteam, etc.), ajouter un mode qui optimise la co-construction :

| Mode | Personas | Peer Review | Best For |
|------|----------|-------------|----------|
| `collaborative` | Builder, Refiner, Validator, Integrator, Challenger | Yes (constructive) | Producing actionable plans and strategies |

Ce mode utiliserait le BUILD-AND-CHALLENGE RULE par défaut et structurerait les rounds comme : idéation → enrichissement → validation → intégration.

---

## 5. Priorisation des changements

| Priorité | Changement | Impact | Effort |
|----------|-----------|--------|--------|
| **P0** | Remplacer ANTI-CONVERGENCE par BUILD-AND-CHALLENGE | Transforme fondamentalement le ton | Faible — 1 bloc de texte |
| **P0** | Assouplir les interdictions des personas | Permet des réponses plus nuancées | Faible — ajustements textuels |
| **P1** | Rééquilibrer le peer review | Évalue la synergie, pas seulement la critique | Faible — ajout de critères |
| **P1** | Restructurer la synthèse Chairman | Priorise la recommandation | Faible — réordonner la structure |
| **P2** | Ajouter le round de co-construction | Nouveau mécanisme collaboratif | Moyen — nouveau step dans le pipeline |
| **P2** | Ajouter le mode collaborative | Option dédiée | Moyen — nouvelles personas |

---

## 6. Résultat attendu

Avec ces corrections, le système devrait passer de :

**Avant :** « Voici pourquoi chaque perspective est en conflit avec les autres, voici les angles morts, voici pourquoi l'accord est suspect. »

**Après :** « Voici ce que le panel a construit ensemble, voici la recommandation qui intègre les meilleures idées de chacun, voici les points de vigilance restants. »

Le désaccord reste un outil — il n'est simplement plus l'objectif.

---

*Rapport généré le 2026-04-01 — Analyse du système AI Provocateurs*
