# ASTERION OS Design Direction

## Core Principle

ASTERION OS is not a generic dark dashboard, sci-fi HUD, cyberpunk control panel, or AI-generated SaaS mockup.

It is a professional autonomous observatory operating system.

The interface must feel like a real astronomical mission-control environment with macOS-level polish.

The most important design rule is:

**Do not mix all references into every screen.**

Each reference has a specific role.
The dashboard, homepage, command palette, telemetry cards, alert system, sky visualization, and observation workflow must each follow different reference priorities.

---

# 1. Product Identity

## Name

**ASTERION OS**

## Description

ASTERION OS is an autonomous observatory operating system that integrates telescope control, weather monitoring, target selection, scheduling, image quality assessment, and astronomical data analysis into one operational intelligence platform.

## Design Keywords

* professional
* calm
* precise
* observatory-grade
* mission-control
* macOS-like
* high information density
* low visual noise
* operational clarity
* scientific intelligence

## Avoid

* generic AI dashboard look
* cyberpunk neon
* excessive glow
* meaningless gradients
* decorative uppercase labels
* random blue/purple/green chips
* toy-like cards
* sci-fi game HUD
* using monospace everywhere
* mixing all visual references into one style

---

# 2. Reference Separation Rule

Each reference must be used only for its assigned area.

| Reference     | Use For                                              | Do Not Use For                 |
| ------------- | ---------------------------------------------------- | ------------------------------ |
| Raycast       | Homepage, command palette, fast OS-like interactions | Full dashboard layout          |
| Linear        | Overall polish, typography, spacing, calm dark mode  | Scientific telemetry structure |
| NASA Open MCT | Main observatory console structure                   | Branding homepage              |
| Grafana       | Metrics, time-series graphs, telemetry density       | Overall product aesthetic      |
| Datadog       | Safety status, alerts, reasons, incidents            | Sky visualization              |
| SkyPortal     | Astronomical target-centered data pages              | Main control dashboard style   |
| LCO Portal    | Observation request workflow                         | Visual style                   |
| NASA Eyes     | Sky dome, target position, celestial visualization   | Full dashboard layout          |

The interface should not look like a collage of these tools.
Each area should have one dominant reference and one secondary reference.

---

# 3. ASTERION Product Areas

## 3.1 ASTERION Home

Purpose: public-facing product/brand website.

Primary references:

* Raycast
* Linear

Design direction:

* Sleek product introduction
* Strong hero section
* Command-palette inspired interaction
* Minimal dark interface
* Polished gradients only if subtle
* Clear product positioning

Should feel like:

* A serious OS product
* A premium developer/scientific tool
* A system with intelligence and speed

Should not feel like:

* A space-themed portfolio
* A NASA fan website
* A sci-fi game landing page

Example hero concept:

```text
ASTERION OS
Autonomous Observatory Intelligence Platform

Plan, execute, monitor, and analyze astronomical observations through one integrated operating system.
```

Command-palette preview:

```text
⌘K
> GoTo M67
> Run autofocus
> Start exoplanet transit sequence
> Evaluate last frame quality
> Open NGC 2261 target page
```

---

## 3.2 ASTERION Console

Purpose: main Muuri-based integrated observatory dashboard.

Primary references:

* NASA Open MCT

Secondary reference:

* Linear

Design direction:

* Professional mission-control dashboard
* Modular cards
* Draggable/resizable panels
* Clear operational hierarchy
* Real-time status visibility
* Calm macOS-like dark material

Should contain:

* Sky Monitor
* Mount
* Camera
* Focuser
* Weather / Safety
* Current Observation
* Frame Preview
* Night Timeline
* Logs
* Quality Metrics

Should feel like:

* A real observatory control room
* Mission operations software
* Scientific monitoring software

Should not feel like:

* Raycast homepage
* Generic SaaS dashboard
* Cyberpunk cockpit
* Gaming HUD

Card hierarchy:

1. Primary operation cards

   * Sky Monitor
   * Current Observation
   * Safety / Weather

2. Control cards

   * Mount
   * Camera
   * Focuser
   * Scheduler

3. Monitoring cards

   * Latest Frame
   * Telemetry
   * Night Timeline
   * Logs
   * Quality Metrics

---

## 3.3 ASTERION Command

Purpose: command palette for fast OS-like operation.

Primary reference:

* Raycast

Design direction:

* Fast keyboard-first control
* Search targets
* Execute system commands
* Open panels
* Run observation workflows
* Show command suggestions

Example commands:

```text
GoTo M67
Start 60s exposure in r
Run autofocus
Create skyflat sequence
Show rejected frames
Open target page: NGC 2261
Abort all operations
Close dome
```

Command palette should feel powerful and minimal.

It should not replace the dashboard.
It is a fast interaction layer above the dashboard.

---

## 3.4 ASTERION Telemetry

Purpose: equipment, weather, and data quality metrics.

Primary reference:

* Grafana

Secondary reference:

* NASA Open MCT

Design direction:

* Dense but readable metric panels
* Time-series graphs
* Threshold-based colors
* No decorative colors
* Clear units
* Compact scientific values

Metrics may include:

```text
CCD temperature
Focuser temperature
Humidity
Dew point gap
Wind speed
Cloud index
FWHM
Eccentricity
Median ADU
Star count
SNR
Background level
Guiding RMS
```

Colors must represent state only:

* normal
* warning
* danger
* inactive

Do not use graph colors as decoration.

---

## 3.5 ASTERION Watchtower

Purpose: weather, safety, and system closure logic.

Primary reference:

* Datadog

Design direction:

* Status must explain cause
* Alerts must show reason
* Safety decisions must be traceable
* Blocking systems must be visible

Bad:

```text
SAFE_CLOSED
```

Good:

```text
SAFE_CLOSED

Primary reason
Sun altitude +46.4° above limit

Blocking systems
- Watchtower: closed
- Skyflat: idle
- Capture: idle

Next observing window
19:42 KST estimated

Recommended action
Wait until civil twilight ends
```

Watchtower should not be just a weather card.
It is the safety decision layer of ASTERION OS.

---

## 3.6 ASTERION Skygraph

Purpose: astronomical ontology and target-centered data structure.

Primary reference:

* SkyPortal

Design direction:

* Object-centered interface
* Targets, observations, frames, quality metrics, and analysis results connected as one knowledge graph
* Data should not be file-folder-centered
* Every observation should belong to a target, purpose, condition, and result

Bad data structure:

```text
2026-06-13/M67/r/Light_001.fit
```

Good data structure:

```text
Target: M67
- Visibility tonight
- Observation history
- Frames
- Filters
- Photometry
- Quality metrics
- Calibration products
- Notes
- Recommended next observation
```

Skygraph is not just a database.
It is the semantic map of the observatory.

---

## 3.7 ASTERION Meridian

Purpose: observation request and scheduling workflow.

Primary reference:

* LCO Observation Portal

Design direction:

* Structured observation request creation
* Target selection
* Filter and exposure planning
* Constraint input
* Priority assignment
* Schedule submission
* Autonomous scheduling by ASTERION OS

Example workflow:

```text
Create Observation Request

1. Target
   M67

2. Science Goal
   CMD calibration

3. Filters
   g, r, i

4. Exposure Strategy
   60s × 10 per filter

5. Constraints
   altitude > 40°
   moon distance > 30°
   seeing < 3"
   twilight: astronomical

6. Priority
   medium

7. Submit to Meridian
```

Meridian should feel like a scientific observation planner, not a generic form.

---

## 3.8 ASTERION Skyview

Purpose: sky visualization and celestial context.

Primary reference:

* NASA Eyes

Secondary reference:

* scientific planetarium interfaces

Design direction:

* Sky dome
* Target position
* Sun and Moon
* Twilight zones
* Horizon limits
* Telescope pointing direction
* Observable region
* Slew path if needed

Skyview should be visually beautiful but not decorative.

It must support operational decisions.

Avoid:

* excessive 3D animation
* educational planetarium look
* bright sci-fi colors
* decorative stars everywhere

---

# 4. Visual System

> **Resolved 2026-06 — supersedes the earlier navy / glass direction.** Anchor reference: **NASA Open MCT (Espresso theme).** The principle behind every rule below: separate surfaces by *luminance*, reserve colour for *state*, and remove effects rather than add them. Near-black navy + cyan + glow + 16px radius reads as "AI SaaS / sci-fi"; a neutral mid-charcoal + one restrained accent reads as "instrument."

## Color

Neutral charcoal dark theme — **not** near-black, **not** blue-tinted. Surfaces are opaque and separated by small luminance steps (~+8% per level), not by borders.

```css
--bg-0:    #2a2a2c;   /* app background — neutral charcoal, zero blue tint */
--bg-1:    #242427;   /* darker surface: drawers / overlay base */
--panel:   #323236;   /* cards = body +~8% (opaque) */
--panel-2: #3a3a3e;   /* inner surfaces: inputs, bars, buttons = +~14% */
--edge:        rgba(255,255,255,.06);   /* hairline — used sparingly */
--edge-strong: rgba(255,255,255,.12);

--text:  #ECEEF1;   /* primary: titles + emphasised values */
--dim:   #AAB0B8;   /* secondary: labels */
--faint: #767880;   /* meta, tags (de-emphasised) */

--accent:      #4DB8FF;                 /* interaction / selection ONLY */
--accent-soft: rgba(77,184,255,.16);

--ok: #5FD08A;  --warn: #E3B341;  --err: #E5706E;  --idle: #8B8D95;
```

Rules:

* Background is neutral mid-charcoal. Counterintuitively, *lighter + neutral* looks more professional than *darker + navy*.
* Separate surfaces by **luminance steps**, not 1px borders. A border is a hairline, used only where luminance alone is not enough.
* Base text is gray (`--dim`); reserve near-white (`--text`) for titles and emphasised numeric values.
* `--accent` is the only chromatic colour in the chrome — it marks the *active / selected* element, never decoration. (Open MCT works because ~95% of it is neutral, so one accent reads as "active.")
* No glow, no coloured shadows, no `backdrop-filter` blur on inline panels.

---

## Typography

One engineered family covering Korean + Latin + numerals.

```css
--sans: "IBM Plex Sans KR", Pretendard, -apple-system, "Malgun Gothic", system-ui, sans-serif;
--mono: "IBM Plex Mono", ui-monospace, "SF Mono", Consolas, monospace;
```

Rules:

* UI font for labels, buttons, titles, descriptions. (Inter dropped — too generic / "SaaS-default".)
* Monospace **only** for time, coordinates, sensor values, exposure values, and technical status — not labels or buttons.
* In a telemetry UI the *numeric mono* carries most of the perceived "font feel" — choose it deliberately.
* Do not overuse uppercase or letter-spacing.
* **Offline domes:** vendor the fonts locally (no CDN `<link>`) so the console runs without internet.

Type scale:

```text
App title: 18px / 700
Page title: 16px / 650
Card title: 14px / 650
Body: 13px / 400
Label: 12px / 500
Numeric value: 13px / 550 monospace
Small metadata: 11px / 500
```

---

## Surfaces & elevation

Opaque charcoal panels, separated by luminance — not glass, not glow.

```css
--radius:    8px;   /* cards — not 16px; instrument, not toy. Use 6 for sharper. */
--radius-sm: 5px;   /* buttons, chips, inputs */

--shadow:         0 1px 2px rgba(0,0,0,.35);   /* inline panels = nearly flat */
--shadow-overlay: 0 8px 28px rgba(0,0,0,.55);  /* ONLY menus / drawers / overlays */
```

```css
.card {
  background: var(--panel);          /* opaque charcoal */
  border: 1px solid var(--edge);     /* hairline */
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  /* no backdrop-filter */
}
```

Elevation is purposeful — inline panels stay flat; only true overlays (menus, drawers) float.

Avoid:

* glowing borders, coloured shadows, glassmorphism blur
* a border on every surface (separate by luminance instead)
* 16px+ radii (toy-like)
* decorative badges, decorative uppercase tags
* every card looking equally important

---

## Buttons

Hierarchy comes from **fill weight, not colour or glow**: solid → surface → text-only.

| Tier | Style | Use | Count |
|------|-------|-----|-------|
| **1 · Primary** | solid accent fill, light text | the main verb (Start, GoTo, Submit) | **one per panel** |
| **2 · Secondary** | surface fill (`--panel-2`), muted text | common controls (Tracking, Park, Configure) | many |
| **3 · Ghost** | text only, no fill / border | cancel, close, clear | many |
| **4 · Danger** | calm: soft-red text on faint red fill | destructive (Stop) | as needed |
| **5 · Emergency** | solid red, always prominent | safety-critical only (Emergency stop, Close dome) | rare |

Rules:

* Hover = `filter: brightness(1.1)`. **Never** a border-highlight or glow.
* Exactly one Primary per context. If two buttons look primary, neither reads as primary.
* Danger is calm by default and intensifies on hover — don't make every Stop a glowing red, or the colour stops meaning "danger."
* Emergency is the *one* exception to "calm": always loud red, and separated by spacing to prevent mis-clicks.

---

# 5. Dashboard Layout Rule

The main dashboard should use a Muuri-style draggable grid.

Grid:

* 12 columns
* 16px gutter
* 20px outer margin
* draggable cards
* resizable cards if possible
* persistent layout state

Important cards must be larger.

Suggested first layout:

```text
Top Bar
- ASTERION OS
- UTC / KST / LST
- Simulation status
- Safety status
- Settings

Row 1
- Sky Monitor
- Current Observation / Auto Sequence

Row 2
- Mount
- Camera
- Focuser
- Watchtower

Row 3
- Latest Frame
- Night Timeline
- Telemetry / Logs
```

---

# 6. Main Rule for Claude

When implementing ASTERION OS, do not simply make it look futuristic.

Make it look operational.

Every visual decision must answer one of these questions:

1. What is the current observing state?
2. Is the system safe?
3. What is the telescope doing?
4. What is the camera doing?
5. What target is being observed?
6. Are the frames scientifically usable?
7. What is the next recommended action?
8. Why did the OS make this decision?

If a UI element does not help answer one of these questions, remove it.
