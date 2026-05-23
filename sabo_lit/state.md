# State

## Fait
- Phase 0 + 0.1 + 0.2 contrats : DONE
- Phase 1 livrable 1 : `DependencyPolicy.scan` + CI (18 tests vert, scan clean)
- Phase 1 livrable 2 step 1 : `LiquidityMapper` (25 tests vert, scan clean)
- Sandbox : loader XAUUSD M5 + integrity check (688 608 bougies, 24/7 continu, 0 trous)
- Sandbox eval LiquidityMapper XAUUSD M5 12-16 mai 2025 : stats + viz HTML
- Phase 0.3 contract : `zone_id` déterministe (sha256[:16]), SESSION→ROLLING. 46 tests vert, scan clean, ruff clean (prod+tests)
- Phase 1 step 2 : `InducementPatternDetector` + `PhaseClassifier`. 100 tests vert, scan clean, ruff clean (prod+tests)
- Phase 1 step 3 : `RuleBasedStructureValidator` (le gate). 124 tests vert, scan clean, ruff clean (prod+tests). **LIT pipeline complet.**
- Sandbox eval bout-en-bout pipeline LIT (XAUUSD M5, 12-16 mai 2025) : 11 gate setups, viz HTML. Ruff sandbox clean. Anciens scripts mappers adaptés au renaming `rolling_*`.

## En cours
- (rien)

- Phase 0.4 contract (option A) : ABC `SweepDetector` ajoutée core. `event_id` `InducementEvent` déterministe (sha256[:16]). ConstructionRule `SweepEvent` rédigée mais DORMANT (commentée) jusqu'à Phase 1.4. Doc CONVENTIONS + PHASE_PLAN mis à jour. 125 tests vert, scan clean, ruff clean.
- Phase 1.4 : `RuleBasedSweepDetector` (sweep_id sha256[:16] déterministe). ConstructionRule `SweepEvent` ACTIVÉE. Fixture `_sweep` test_phase_classifier re-routée via vrai détecteur. Sandbox `_synth_sweep` supprimé, vrai sweeper branché. 151 tests vert, scan clean, ruff clean (core + lit + governance + tests + sandbox). Sandbox montre PHASE_2_MITIGATION 302 ticks / PHASE_1 16 / UNDEFINED 1122. Les 11 gate setups deviennent tous PHASE_2_MITIGATION.

- Sandbox viz LIT pipeline patched : phase lue depuis setup, marqueurs triangles direction-aware (vert haut / rouge bas), zones alpha 0.15, annotation P&L compacte +50/+100 candles, stats agrégées print.
- Backtest LIT 6 mois XAUUSD M5 (2024-11-19 → 2025-05-19) : 52 129 candles, 173 gates (3.32/k candles), final +25 237p (horizon 100), max DD 11 093p (79.6%), longest losing streak 7. Asymétrie nette : bullish PF 2.08 / bearish PF 0.80. Equal_lows + PDL portent le edge ; equal_highs + PDH négatifs. Run 165s.
- Backtest multi-fenêtres 3 régimes (bull/bear/range) : bull +23.9% PF 1.27 final +25 237p ; bear -10.4% PF 0.86 final -14 878p ; range +3.8% PF 0.94 final -5 481p. Bull_PF survit en range (0.91) mais s'effondre en bear (0.98 indistinguable de breakeven). Bear_PF jamais > 1 (0.75-0.98). Edge unilatéral trend-dépendant. Run 554s (9.2 min).

## Prochaine action
Phase 2 : regime engine + secondary filters. Note : `ValidatedStructureState.sweep_event_id` reste `None` car `StructureValidator.validate(induc, phase, candles)` n'accepte pas de sweep — wiring sweep dans validator output = contract phase à part (changement ABC).
