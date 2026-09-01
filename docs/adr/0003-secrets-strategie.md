# ADR 0003: Secrets- und Credentials-Verwaltungsstrategie

**Status:** angenommen

**Datum:** 2026-08-31

## Kontext

Services müssen externe Credentials speichern (API-Keys, Datenbank-Passwörter, etc.) und diese ruhend verschlüsseln. PaddleDoc nutzt heute Fernet mit HKDF-SHA256-Ableitung; ein Nachteil ist, dass bei Key-Rotation alle bestehenden verschlüsselten Werte invalid werden.

Die neue Strategie muss:

1. Ruhende Credentials verschlüsseln
2. Key-Rotation ohne Datenverlust ermöglichen
3. Pro Service unabhängig arbeiten

## Entscheidung

### Jeder Service hat einen eigenen SECRET_KEY

- Keine Shared-Secrets zwischen Services
- Nicht in `settings.py`, sondern in Umgebungsvariablen (`SECRET_KEY`) oder Vault
- Format: Base64-kodiert, mindestens 32 Bytes

### Verschlüsselung: Fernet + HKDF-SHA256 (wie PaddleDoc)

```python
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

def derive_key(master_secret: str, salt: str = "credentials") -> str:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode(),
        info=b"weave-credentials",
    )
    key = hkdf.derive(master_secret.encode())
    return base64.urlsafe_b64encode(key).decode()

cipher = Fernet(derive_key(SECRET_KEY))
encrypted = cipher.encrypt(credential_value.encode())
```

### Neu: Key-Rotation mit versionierter Key-Liste

**Umgebungsvariable `SECRET_KEYS`:**
```
SECRET_KEYS=current_key,old_key_1,old_key_2
```

**Verhaltensweise:**
- **Verschlüsseln:** Immer mit dem ersten Key (aktiven Key)
- **Entschlüsseln:** Über Liste fallback (aktuell → alt1 → alt2)
- **Re-Encrypt-Job:** Nightly-Task, der Bestandsdaten mit altem Key entschlüsselt und mit neuem Key erneut verschlüsselt

**Rotation-Prozess:**
1. Neuen Key generieren
2. `SECRET_KEYS = new_key + "," + SECRET_KEYS` setzen
3. Re-Encrypt-Job starten (optional; kann über Zeit laufen)
4. Nach Ablaufzeit alte Keys entfernen

## Konsequenzen

**Positiv:**
- Kein Datenverlust bei Key-Rotation
- Graduelles Migrieren von alten zu neuen Keys
- Audit-Trail: Welche Credentials wann rotiert wurden
- Einfache Implementierung

**Negativ:**
- Mehrere Keys im Speicher (Speicher vs. Sicherheit Trade-off)
- Re-Encrypt-Job muss reliable laufen

## Alternativen

1. **Hardware-Security-Module (HSM):**
   - Zu komplex für MVP; später evaluieren

2. **Vault (HashiCorp):**
   - Externe Abhängigkeit; PaddleDoc nutzt heute nicht

3. **Jeder Credential einzeln versioniert:**
   - Zu granular; erhöht DB-Komplexität
