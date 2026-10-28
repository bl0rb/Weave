# weave-mark

Die eine Quelle für das Weave-Zeichen und seine Bewegungen. Die drei
Oberflächen (Weave-Chat, Weave-Ingest-Portal, Desktop-App) bauen getrennt
und bringen deshalb je eine Kopie mit; die Desktop-App liegt in
[bl0rb/WeaveDesktop](https://github.com/bl0rb/WeaveDesktop):

| Was | Kanonisch | Kopien |
| --- | --- | --- |
| Zeichen (Inline-SVG) | `weave-mark.svg` | `services/chat/src/components/weave-logo.tsx`, `services/ingest/frontend/src/components/weave-ingest-logo.tsx`, WeaveDesktop: `ui/common.js` (`WEAVE_MARK`) |
| Keyframes der Bewegungen | `motion.css` | `services/chat/src/app/globals.css`, `services/ingest/frontend/src/app/globals.css`, WeaveDesktop: `ui/style.css` |
| Auswahl der Bewegung | — | `services/chat/src/lib/weave-motion.ts`, `services/ingest/frontend/src/lib/weave-motion.ts`, WeaveDesktop: `ui/common.js` (`nextWeaveMotion`) |

`scripts/check_weave_mark.py` vergleicht Zeichen-Geometrie und Keyframes der
Kopien in diesem Repository mit der kanonischen Fassung (die Kopie in
WeaveDesktop wird von Hand nachgezogen); `scripts/tests/test_weave_mark_sync.py`
lässt den Abgleich im CI-Job „Platform configuration" mitlaufen. Eine
Änderung am Zeichen oder an einer Bewegung beginnt hier und wird dann in
alle Kopien übernommen, auch in WeaveDesktop.

Bewegungen (`weave`, `turn`, `flip`, `shuttle`): bei jedem Start wird eine
zufällig gewählt, nie die zuletzt gelaufene (`localStorage`-Schlüssel
`weave-motion-last`). Ein Klick auf das Zeichen wechselt zur nächsten; die
letzte Runde einer endlichen Animation endet mit einem weichen `weave-settle`.
