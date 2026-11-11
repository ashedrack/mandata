# System Design Diagrams

Visual reference for how Mandata's components interact. All diagrams are
[Mermaid](https://mermaid.js.org) — they render on GitHub and can be edited
in any Mermaid editor (mermaid.live).

---

## 1. The mandate chain — system overview

```mermaid
flowchart LR
    subgraph Actors
        P[Principal<br/><i>the human or org granting authority</i>]
        A[Agent<br/><i>AI system proposing transactions</i>]
    end

    subgraph Mandata["Mandata"]
        M[Mandate<br/>id · bounds · expiry]
        C[Credential<br/>Ed25519 binding]
        G["enforce()<br/>deterministic gate"]
        E[(EvidenceLog<br/>hash-chained records)]
    end

    R[Payment Rail<br/>AP2 · x402 · bank]

    P -- "1. issues mandate + signs credential" --> M
    C -. "binds" .-> M
    C -. "binds" .-> A
    A -- "2. proposes transaction" --> G
    G -- "evaluate bounds" --> M
    G -- "3. append decision" --> E
    G -- "allowed ✓" --> R
    G -. "rejected ✗<br/>never reaches rail" .-> A
    R -- "5. settlement" --> P
```

---

## 2. Enforcement gate — decision flow

Every `enforce()` call evaluates conditions in order. First failure wins.

```mermaid
flowchart TD
    T([Transaction proposed]) --> Q1{mandate<br/>revoked?}
    Q1 -- yes --> X1["REJECT<br/>'mandate revoked'"]
    Q1 -- no --> Q2{expired?}
    Q2 -- yes --> X2["REJECT<br/>'mandate expired'"]
    Q2 -- no --> Q3{amount > 0?}
    Q3 -- no --> X3["REJECT<br/>'non-positive amount'"]
    Q3 -- yes --> Q4{amount ≤ cap?}
    Q4 -- no --> X4["REJECT<br/>'exceeds cap'"]
    Q4 -- yes --> Q5{currency<br/>matches?}
    Q5 -- no --> X5["REJECT<br/>'outside mandate'"]
    Q5 -- yes --> Q6{payee in<br/>allowlist?}
    Q6 -- no --> X6["REJECT<br/>'not in allowed set'"]
    Q6 -- yes --> Q7{velocity<br/>limit ok?}
    Q7 -- no --> X7["REJECT<br/>'daily limit reached'"]
    Q7 -- yes --> OK["ALLOW<br/>'within mandate'"]

    X1 & X2 & X3 & X4 & X5 & X6 & X7 & OK --> EV[(EvidenceLog<br/>every decision recorded)]
    OK --> RAIL[[→ payment rail]]

    style X1 fill:#f8d7da
    style X2 fill:#f8d7da
    style X3 fill:#f8d7da
    style X4 fill:#f8d7da
    style X5 fill:#f8d7da
    style X6 fill:#f8d7da
    style X7 fill:#f8d7da
    style OK fill:#d4edda
    style RAIL fill:#d4edda
```

---

## 3. Mandate lifecycle — state diagram

```mermaid
stateDiagram-v2
    [*] --> Issued : principal grants
    Issued --> Active : credential signed
    Active --> Expired : expiry reached
    Active --> Revoked : principal revokes
    Expired --> [*]
    Revoked --> [*]

    note right of Active
        enforce() accepts
        transactions here only
    end note
    note right of Revoked
        takes effect at the
        NEXT enforce() call —
        revocation must beat
        the transaction
    end note
```

---

## 4. End-to-end sequence — the Alice/ShopBot scenario

```mermaid
sequenceDiagram
    autonumber
    actor Alice as Alice<br/>(principal)
    participant SB as ShopBot<br/>(agent)
    participant G as Mandata Gate<br/>enforce()
    participant EL as EvidenceLog
    participant R as Payment Rail<br/>(AP2/x402 → bank)

    Alice->>SB: issue mandate<br/>£500 · GBP · tesco+skyscanner · 30d · 3/day
    Alice->>SB: sign credential (Ed25519)

    Note over SB: decides to buy £420 flight
    SB->>G: propose tx: £420 → skyscanner.com
    G->>G: checks: amount ✓ payee ✓ expiry ✓ velocity ✓
    G->>EL: append ALLOWED record (hash-chained)
    G-->>SB: Decision: allowed + evidence receipt
    SB->>R: execute payment £420
    R-->>Alice: flight booked ✓

    Note over SB: compromised by injected content
    SB->>G: propose tx: £600 → unknown-payee.io
    G->>G: checks: over cap ✗ unlisted payee ✗
    G->>EL: append REJECTED record
    G-->>SB: Decision: rejected + reason
    Note over R: payment NEVER reaches the rail

    Note over Alice,EL: months later — dispute/audit
    Alice->>EL: verify_chain()
    EL-->>Alice: every proposal, allowance, refusal — provable
```

---

## 5. Evidence chain — tamper-evidence structure

```mermaid
flowchart LR
    R1["Record 1<br/><i>allow £420</i><br/>prev: genesis"] --> R2["Record 2<br/><i>allow £30</i><br/>prev: hash(R1)"]
    R2 --> R3["Record 3<br/><i>REJECT £600</i><br/>prev: hash(R2)"]
    R3 --> R4["Record 4<br/>...<br/>prev: hash(R3)"]

    T[/"Tamper with Record 2<br/>→ its hash changes<br/>→ Record 3's prev_hash mismatches<br/>→ verify_chain() FAILS"/] -.-> R2

    style T fill:#fff3cd
```

---

## 6. Where Mandata sits — stack diagram

```mermaid
flowchart TB
    subgraph L1["Agent layer — probabilistic"]
        AF["LangChain · crewAI · custom agents<br/><i>forms intent — may err, may be steered</i>"]
    end
    subgraph L2["Mandata — deterministic judgment"]
        MG["enforce() gate<br/>same inputs → same answer, always<br/>+ evidence for every decision"]
    end
    subgraph L3["Settlement layer — irreversible"]
        PR["AP2 · x402 · card networks · bank rails<br/><i>moves value exactly once</i>"]
    end

    AF -- "proposes" --> MG
    MG -- "allowed only" --> PR
    MG -. "rejects stay here" .-> AF

    style L1 fill:#fff3cd
    style L2 fill:#d1ecf1
    style L3 fill:#f8d7da
```

> **The architectural rule:** agency lives above the gate; only checked,
> bounded, authorized actions pass through it. *The closer to settlement,
> the less agency a transaction can safely carry.*

---

## 7. Module map — the codebase itself

```mermaid
flowchart TB
    subgraph pkg["src/mandata/"]
        MOD[models.py<br/>Mandate · Bounds · Decision · EvidenceRecord]
        ENF[enforce.py<br/>the gate]
        EV[evidence.py<br/>EvidenceLog · verify_chain]
        CR[credentials.py<br/>issue_credential · verify_credential<br/>Ed25519 · PyNaCl]
    end

    MOD --> ENF
    MOD --> EV
    ENF --> EV
    CR --> MOD
    ENF -. "optional:<br/>verify credential first" .-> CR
```
