# ASTREION OS Design Direction

## Core Principle

ASTREION OS is not a generic dark dashboard, sci-fi HUD, cyberpunk control panel, or AI-generated SaaS mockup.

It is a professional autonomous observatory operating system.

The interface must feel like a real astronomical mission-control environment with macOS-level polish.

The most important design rule is:

**Do not mix all references into every screen.**

Each reference has a specific role.
The dashboard, homepage, command palette, telemetry cards, alert system, sky visualization, and observation workflow must each follow different reference priorities.

---

# 1. Product Identity

## Name

**ASTREION OS**

## Description

ASTREION OS is an autonomous observatory operating system that integrates telescope control, weather monitoring, target selection, scheduling, image quality assessment, and astronomical data analysis into one operational intelligence platform.

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

# 3. ASTREION Product Areas

## 3.1 ASTREION Home

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
ASTREION OS
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

## 3.2 ASTREION Console

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

## 3.3 ASTREION Command

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

## 3.4 ASTREION Telemetry

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

## 3.5 ASTREION Watchtower

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
It is the safety decision layer of ASTREION OS.

---

## 3.6 ASTREION Skygraph

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

## 3.7 ASTREION Meridian

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
* Autonomous scheduling by ASTREION OS

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

## 3.8 ASTREION Skyview

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

## Color

Use a restrained dark theme.

Background:

```css
--background: #080A0F;
--surface-1: rgba(18, 22, 30, 0.82);
--surface-2: rgba(25, 30, 40, 0.76);
--border: rgba(255, 255, 255, 0.07);
```

Text:

```css
--text-primary: #E6EAF0;
--text-secondary: #9AA4B2;
--text-muted: #687386;
--text-disabled: #3F4858;
```

Accent:

```css
--accent: #4DB8FF;
```

Status:

```css
--safe: #4ADE80;
--warning: #FBBF24;
--danger: #F87171;
--idle: #8B95A7;
```

Rules:

* Accent color is not decoration.
* Status colors are used only for actual system state.
* Avoid random purple, cyan, green, yellow combinations.
* Do not make everything glow.

---

## Typography

Use two font systems only.

Primary UI font:

```css
Inter, Pretendard, system-ui, -apple-system, BlinkMacSystemFont, sans-serif
```

Monospace font:

```css
SF Mono, JetBrains Mono, IBM Plex Mono, monospace
```

Rules:

* Use primary font for labels, buttons, titles, descriptions.
* Use monospace only for time, coordinates, sensor values, exposure values, and technical status.
* Do not use monospace everywhere.
* Do not overuse uppercase.
* Do not add excessive letter-spacing.

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

## Cards

Cards should feel like macOS dark material panels.

Card style:

```css
.card {
  border-radius: 16px;
  background: rgba(18, 22, 30, 0.78);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.07);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28);
}
```

Avoid:

* glowing blue borders
* thick outlines
* overly bright card backgrounds
* decorative badges
* every card looking equally important

---

## Buttons

Button types:

1. Primary action

   * Execute
   * Start
   * GoTo
   * Submit

2. Secondary action

   * Configure
   * Open
   * Preview

3. Danger action

   * Stop
   * Abort
   * Emergency close

Rules:

* Buttons should be calm and operational.
* No neon glow.
* Danger buttons should be used only for dangerous actions.
* Use clear labels.

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
- ASTREION OS
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

When implementing ASTREION OS, do not simply make it look futuristic.

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
