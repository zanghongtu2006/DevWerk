# DevWerk IntelliJ-Plugin

Das DevWerk IntelliJ-Plugin ist ein Capability Provider und die lokale
Sicherheitsgrenze für den DevWerk-Dienst. Es sammelt Workspace-Evidenz, stellt
semantische Werkzeuge bereit, sendet Workflow-Anfragen an den Dienst und wendet
freigegebene Dateioperationen in einem IntelliJ-Projekt an.

Kanban-Zustand und Workflow-Definition gehören zum FastAPI-Dienst unter
`../DevWerk/`, nicht zum Plugin.

## Aktueller Stand

- Plugin-Version: `0.0.1`
- IntelliJ-Platform: `2024.1` (`sinceBuild=241`, `untilBuild=243.*`)
- JVM-Ziel: Java 17
- Oberfläche: rechtes **DevWerk**-Toolfenster
- Entwicklungsstatus: pausiert, bis der eigenständige DevWerk-Version-1-Dienst
  Aufgaben selbstständig abschließen kann; danach wird die Plugin-Arbeit fortgesetzt

Die früheren Endpunkte `/v1/plan` und `/v1/execute` gehören nicht mehr zum
aktuellen Dienstvertrag. Neue Integrationen sollen `/v1/workflows`,
Workflow-Ereignisse/-Nachrichten und semantische Aktionen verwenden.

## Aufgaben des Plugins

- Projektbaum, Source Map, geöffnete/geänderte Dateien und Diagnosen erfassen
- Workspace-Operationen zum Auflisten, Lesen und Suchen bereitstellen
- Kompilierungs-, Prozess- und IDE-Evidenz liefern, sofern unterstützt
- strukturierte Dateioperationen innerhalb des geschützten Projektstamms anwenden
- Vorher-/Nachher-Snapshots für Quellcodeänderungen erstellen
- DevWerk-Workflows im IntelliJ-Toolfenster darstellen

## Sicherheitsmodell

`SnapshotGuard` sichert die betroffenen Dateien vor einer Änderung und prüft
die Vollständigkeit der Snapshots vor dem Apply-Schritt. Nach erfolgreicher
Ausführung wird der neue Zustand erfasst. Pfadprüfungen verhindern Zugriffe
außerhalb des Projektstamms.

Generierte Änderungen müssen weiterhin geprüft werden. Eine erfolgreiche
HTTP-Antwort ersetzt weder die Kontrolle der geänderten Pfade noch IDE-Diagnosen
oder Workflow-Artefakte.

## Build

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

Weitere Aufgaben:

```powershell
.\gradlew.bat test
.\gradlew.bat runIde
.\gradlew.bat buildPlugin
```

Build-Ausgaben liegen unter `build/`.

## Struktur

```text
src/main/kotlin/com/zanghongtu/devwerk/
  DevWerkFsToolWindowPanel.kt   Toolfenster
  DevwerkOperationRunner.kt    geschützte Operationsausführung
  SnapshotGuard.kt             Vorher-/Nachher-Snapshots
  codeEditor/HttpAiClient.kt   Dienstclient und Workflow-Polling
  codeEditor/SourceMapBuilder.kt
  codeEditor/WorkspaceTools.kt
  settings/                    lokale Provider-Einstellungen
src/main/resources/META-INF/plugin.xml
```

## Konfiguration und Datenschutz

Provider-URLs, Modelle und Tokens werden in den lokalen IDE-Einstellungen
gespeichert. Zugangsdaten dürfen weder committed noch in Fehlerberichte kopiert
werden. Ob Workspace-Inhalte die IDE verlassen, hängt von der vom Benutzer
gewählten Dienst- und Provider-Konfiguration ab.

## Lizenz

GNU LGPL 2.1, entsprechend der Lizenz im Repository-Stamm.
