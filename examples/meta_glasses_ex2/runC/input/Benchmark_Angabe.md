# Benchmark-Angabe: Meta AI-Glasses, Design Example 2

Quelle: Zhu et al., "Advancing Artificial Intelligence-Driven Wireless Hardware Design Through Industry Benchmarks", IEEE Antennas & Propagation Magazine, Oktober 2026, Abschnitt "Example #2" und Bild 2. Diese Datei fasst die Angabe zusammen; im Zweifel gilt das Paper.

## Aufgabe

Eine Antenne auf dem Antennen-Flex (FPC) eines vereinfachten Brillenbügels entwerfen, die WLAN 802.11ax abdeckt:

- Band 1: 2401-2483 MHz
- Band 2: 5170-7125 MHz

Zielgröße ist die Total-Effizienz im Freiraum (abgestrahlte Leistung / verfügbare Leistung der 50-Ohm-Quelle, also inklusive Fehlanpassung und Verlusten). Beide Bänder zählen gleich.

## Aufbau

- PCB: unregelmäßige Kontur, laut Bild 2 40 mm x 11.3 mm (in der 3D-Datei 40.7 mm x 10.4 mm), massiv Kupfer (5.8e7 S/m).
- Chassis: vereinfachter Bügel mit Scharnierblock und Frontstreifen, Edelstahl (1.1e6 S/m laut Referenzdatei).
- FPC: ca. 15.2 mm x 2.7 mm nutzbare Fläche (Kontur siehe 3D-Datei), 0.265 mm dick, Dk 3.09, tan d 0.0105 (Bild 2 und Referenzdatei; der Fließtext nennt 0.00105). Zwei Löcher zur Ausrichtung: ein Rundloch und ein Langloch.
- Das FPC liegt neben und unterhalb der PCB-Kante: FPC-Oberseite bei z = 0, PCB-Unterseite bei z = 1.2 mm.

## Regeln

1. Genau eine Speisung. Die Verbindung zwischen PCB und FPC erfolgt über ein Via.
2. Beliebig viele zusätzliche Masseverbindungen zur PCB sind erlaubt, aber nur im gleichen Verbindungsstil wie die Speisung (Via). Lage von Speisung und Masseverbindungen ist frei, solange sie an die PCB anschließen.
3. Die Antenne liegt vollständig innerhalb der FPC-Kontur. Eine oder beide FPC-Lagen dürfen genutzt werden, Vias zwischen den Lagen sind erlaubt.
4. Leiterbahnen halten mindestens 0.1 mm Abstand zu FPC-Kanten und zu den Löchern.
5. FPC, PCB und Chassis dürfen nicht verändert werden.
6. Antennenmetall ist Kupfer; eine 2D-Fläche ohne Dicke ist zulässig.
7. Das Paper erlaubt optional ein Anpassnetzwerk aus höchstens fünf verlustbehafteten 0201-Bauteilen und 0201-Bauteile in den Masseverbindungen. Ob das genutzt werden darf, legt der Prompt fest.

## 3D-Dateien in diesem Paket

- `Ex2_Benchmark_geometry.step`: drei Körper mit Namen `PCB`, `FPC`, `Chassis`, Einheit mm. Keine Antenne, keine Speisung.
- `Ex2_Benchmark.hzn`: dasselbe als Hertzon-Projekt (Design `Ex2_Benchmark`) mit zugewiesenen Materialien, offener Berandung (Auto Open) und einem Setup ohne Sweeps.

| Körper | Material | x / mm | y / mm | z / mm |
|---|---|---|---|---|
| PCB | copper | -4.12 .. 36.54 | -3.81 .. 6.55 | 1.20 .. 1.78 |
| FPC | Dk 3.09, tan d 0.0105 | -3.65 .. 14.63 | 2.94 .. 6.655 | -0.265 .. 0 |
| Chassis | stainless_steel 1.1e6 S/m | -17.36 .. 77.93 | -3.9 .. 8.4 | -0.95 .. 147.5 |

Für die Antenne wichtige Kanten: Die obere PCB-Kante läuft von (-3.5, 3.93) nach (16.19, 4.04). Rundloch im FPC: Mittelpunkt (0.525, 5.315), Radius 0.372 mm. Langloch: Ecken (11.441, 3.49), (11.839, 3.444), (11.926, 4.188), (11.529, 4.235).

### Geprüft

- Die Körper überlappen nicht. PCB und Chassis liegen bündig aufeinander, die sieben Chassis-Blöcke sind zu einem Körper vereinigt.
- Das Hertzon-Projekt und die STEP-Datei (nach erneutem Import) wurden mit einer Testspeisung vernetzt; der STEP-Import wurde zusätzlich mit vier adaptiven Pässen gelöst.

### Abweichungen von Metas Original-STEP

Das Original ließ sich in früheren Läufen nicht direkt vernetzen. Die Körper wurden deshalb aus den STEP-Koordinaten mit Grundkörpern nachgebaut:

- PCB und Chassis sind im Original um 0.49 Grad gegen das FPC geneigt (Abstand FPC zu PCB 1.10 bis 1.27 mm). Hier sind sie eben, Abstand 1.2 mm.
- Die Chassis-Blöcke sind im Original bis zu 5.6 Grad gedreht. Hier sind sie achsparallel.
- Die untere PCB-Kante ist im Original ein Spline, hier eine Gerade. Verrundungen der PCB- und FPC-Kontur sind als Fasen oder Polygonzüge angenähert (Rundloch als 16-Eck).
- Die Körper von Metas Referenz-Speisung (Via mit 0.6 mm Durchmesser bei x = 7.49, y = 4.89, Signal-Pad 1.5 x 1.0 mm in der Ebene der PCB-Unterseite, interne Leiterbahn) sind nicht enthalten. Die Speisung gehört zum Entwurf.

Der Einfluss dieser Abweichungen auf die Effizienz ist in diesem Paket nicht durch eine Vergleichsrechnung belegt. Ein früherer Lauf nennt weniger als einen Prozentpunkt Unterschied zwischen einem solchen Nachbau und dem Original im Referenz-Solver; das wurde hier nicht nachgeprüft.
