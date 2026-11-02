# Ticket 002: URIrun adapter istniejacych tras do rejestru Dockuri

- **ID**: ticket-002
- **Owner**: agent:antigravity
- **Status**: IN_PROGRESS
- **Workflow state**: EDIT
- **Created**: 2026-11-02

## Goal and scope

Eksportować kontrakty wybranych istniejących tras HTTP/MQTT/function do profilu Dockuri, bez budowania drugiego routera lub nowej kolejki.

## Acceptance criteria

- [x] AC-01: Wektory kodowania URI, typów argumentów i błędów zachowują parytet.
- [x] AC-02: Stan transportu i wynik domenowy pozostają rozdzielone.
- [x] AC-03: Nieobsługiwane schematy zwracają typed unsupported; nie ma fallbacku do shell.

## Tracking boundary

This directory contains the minimal reviewed intent. Optional participant prose
and raw command logs are not required delivery output.
