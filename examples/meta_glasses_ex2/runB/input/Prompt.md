# Prompt: Meta Example 2, beste Antenne ohne Anpassnetzwerk

Entwirf für das Benchmark "Meta AI-Glasses, Design Example 2" die bestmögliche Antenne auf dem FPC. Die Angabe steht in `Benchmark_Angabe.md`, die Umgebung (PCB, FPC, Chassis, ohne Antenne) in `Ex2_Benchmark.hzn` und `Ex2_Benchmark_geometry.step`. Arbeite auf einer Kopie des Projekts in einem neuen Unterordner und verändere PCB, FPC und Chassis nicht.

## Ziel

Zielgröße ist die Total-Effizienz im Freiraum, Prad / Pinc an 50 Ohm, der nackten Antenne: kein Anpassnetzwerk, keine konzentrierten Bauteile, nur gedrucktes Kupfer und Vias.

1. Pflicht: In beiden Bändern liegt die Total-Effizienz an jeder Frequenz bei mindestens 40 %. Das gilt für 2401-2483 MHz und für 5170-7125 MHz, einschließlich der Bandkanten. Ein Entwurf, der an irgendeinem Punkt darunter liegt, gilt als nicht bestanden, egal wie gut sein Mittelwert ist.
2. Pflicht: Überall in beiden Bändern muss eine strahlende Mode vorhanden sein. Kein Teilbereich darf nur deshalb über 40 % liegen, weil eine benachbarte Resonanz ausläuft, und es darf keine Lücke geben, in der die Struktur vor allem Energie speichert (starke Reflexion bei hohem Q ohne Resonanz in der Nähe, Strom zirkuliert auf nicht strahlenden Teilen). Belege das für den finalen Entwurf über Impedanz, Resonanzen, Q und Strahlungseffizienz über der Frequenz.
3. Darüber hinaus so gut wie möglich: Maximiere das Mittel der beiden Band-Mittelwerte (beide Bänder gleich gewichtet). Bei gleichem Mittel gewinnt der Entwurf mit dem höheren Minimum.
4. Messlatte ist Run 4 (Entwurf A29, ohne Anpassung): in Hertzon 67.3 % Band-Mittel bei 2.4 GHz und 69.0 % bei 5.17-7.125 GHz (Mittel 68.2 %), im Referenz-Solver 68.7 % / 71.7 % (70.2 %). Die Kurven liegen in `run4_messlatte/` (Total-Effizienz und S11, nur Kurven, kein Layout); schwächster Punkt dort ist 5.17 GHz mit rund 47 %. Das Ergebnis soll besser sein: in Hertzon Mittel über 68.2 % und kein Band schlechter als bei Run 4. Wenn das nicht erreichbar ist, liefere den besten Entwurf, der die 40-%-Pflicht erfüllt, und begründe mit Rechnungen, was die Grenze setzt.

Höre nicht beim ersten Entwurf auf, der die Pflicht erfüllt. Vergleiche mehrere grundsätzlich verschiedene Konzepte, bevor du eines verfeinerst, und optimiere das beste danach weiter, bis weitere Rechnungen keinen Gewinn mehr bringen.

## Regeln

- Es gelten die Regeln aus `Benchmark_Angabe.md`: eine Speisung, Masseverbindungen nur als Vias zur PCB, alles Kupfer innerhalb der FPC-Kontur, mindestens 0.1 mm Abstand zu Kanten und Löchern, eine oder beide FPC-Lagen.
- Entwirf selbst. Metas Referenzantenne nicht nachbauen.
- Von Grund auf: Lies vor dem Entwurf keine Berichte, Geometrien, Projekte oder Notizen früherer Läufe. (Diese Zeile streichen, wenn Vorwissen erlaubt sein soll.)

## Modellierung

- Vias zwischen FPC und PCB sind Kupferzylinder, keine flachen Bänder. Durchmesser angeben und für alle Vias gleich wählen.
- Die Speisung braucht einen sauberen Übergang: Das Speise-Via endet auf einem Signal-Pad, der Port liegt zwischen zwei geraden Leiterkanten (Pad und PCB) und ragt nicht in Metall. Ein Port-Sheet quer über eine runde Stirnfläche ist nicht zulässig. Prüfe die Leistungsbilanz; eine Abweichung über 2 % der einfallenden Leistung heißt, dass der Port nicht stimmt.
- Jede Vereinfachung muss vor dem Bericht durch eine Vergleichsrechnung gegen die genauere Variante belegt sein, mit dem gemessenen Unterschied. Was nicht geprüft ist, wird nicht vereinfacht. Der Auftraggeber soll keine Modellfehler selbst finden müssen.
- Prüfe den finalen Entwurf vor dem Bericht in der 3D-Ansicht: Aussparungen um die FPC-Löcher sitzen an der richtigen Stelle, alle Vias treffen Kupfer auf beiden Seiten, nichts überlappt ungewollt.

## Auswertung des finalen Entwurfs

- Netz konvergiert (Delta S erreicht, zwei Pässe in Folge) und einmal mit feinerem Netz bestätigt. Die 2.4-GHz-Resonanz ist schmal, dort zählt die Frequenzlage.
- Effizienz an diskreten Punkten mit gespeicherten Feldern: mindestens 2.401, 2.42, 2.44, 2.46 und 2.483 GHz sowie 5.17 bis 7.125 GHz in Schritten von höchstens 0.1 GHz, beide Bandkanten eingeschlossen.
- Tabelle mit Mismatch-, Strahlungs- und Total-Effizienz je Frequenz, dazu Minimum und Mittel je Band und die klare Aussage "40-%-Pflicht erfüllt: ja / nein".
- Designregel-Check mit Zahlen: kleinster Abstand zu FPC-Kanten und zu beiden Löchern.

## Arbeitsweise

- Mit Hertzon über die MCP-Schnittstelle, sichtbar in der Hertzon-Oberfläche.
- Zeit, Tokens und Anzahl der Solves je Schritt in `Timing_Log.md` mitschreiben.
- Am Ende ein kurzer Bericht mit Layout-Bild, Ergebnistabelle, Liste der Annahmen mit ihren Prüfrechnungen und dem Vergleich mit der Messlatte. Schwächen offen nennen.
- Keine Releases, keine Merges.
