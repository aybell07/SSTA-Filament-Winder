# Code Walkthrough: SSTA Filament Winder (Cyclone)

This document explains what the code in this repository does, file by file, with most of the detail on `src/planner/`, which does all of the toolpath math.

---

## 1. The big picture

The software does three jobs, and each is a CLI subcommand in `src/cli-entry.ts`:

```
   .wind file (JSON)                         gcode file                      Marlin board
 ┌──────────────────┐   plan    ┌────────────────────────────┐   run    ┌─────────────────┐
 │ mandrel, tow,    │ ────────▶ │ ; Parameters {...}          │ ───────▶ │ moves motors    │
 │ list of layers   │ planner/  │ G0 X0 Y0 Z0                 │ marlin-  │ X = carriage    │
 └──────────────────┘           │ G0 F9000                    │ port.ts  │ Y = mandrel     │
                                │ G0 X1 Y2.34 Z-10.8 ...      │          │ Z = deliv. head │
                                └────────────────────────────┘          └─────────────────┘
                                              │ plot (plotter/)
                                              ▼
                                   PNG of the tow paths "unrolled"
```

| Command | Code | What it does |
|---|---|---|
| `npm run cli -- plan -o out.gcode in.wind` | `src/planner/` | Turns a JSON description of the part into G-code |
| `npm run cli -- plot -o out.png in.gcode` | `src/plotter/` | Draws the G-code onto an "unrolled" mandrel surface so you can check coverage |
| `npm run cli -- run -p /dev/ttyUSB0 in.gcode` | `src/marlin-port.ts` | Streams the G-code to a Marlin board over serial, with pause and resume on the spacebar |

`npm start` launches an Electron window (`main.ts`, `preload.ts`, `renderer.ts`, `index.html`). That is still the Electron "Hello World" template. It is a placeholder for the GUI the README lists as a to-do and has no winder logic.

### The machine the code assumes (3 axes)

| Logical axis (in code) | G-code letter | Units | Physical meaning |
|---|---|---|---|
| `CARRIAGE` | `X` | mm | Carriage sliding along the mandrel's length |
| `MANDREL` | `Y` | **degrees** | Mandrel rotation (Marlin is set up so that `Y360` = one full turn) |
| `DELIVERY_HEAD` | `Z` | **degrees** | Rotation of the payout eye/head so the tow lies flat at the wind angle |

Marlin treats all three as ordinary linear axes. The code relies on the steps/mm setting being tuned so that one "mm" on Y and Z equals one degree.

---

## 2. `src/planner/`: the toolpath generator

```
planner/
├── index.ts     re-exports planner.ts and types.ts
├── types.ts     shapes of the .wind file, axis enums, coordinate types
├── helpers.ts   serializeCoordinate(), interpolateCoordinates()
├── machine.ts   WinderMachine: a "virtual machine" that emits G-code and tracks state
└── planner.ts   planWind() + one planner per layer type (hoop, helical, skip)
```

The layers depend on each other like this:

```
planner.ts  ── decides WHAT motion to make (the winding math)
    │ calls machine.move(), machine.setPosition(), machine.zeroAxes() ...
    ▼
machine.ts  ── decides HOW to express it as G-code (segmenting, formatting, profiling)
    │ uses
    ▼
helpers.ts / types.ts / ../helpers.ts (stripPrecision, degToRad, radToDeg)
```

### 2.1 `types.ts`: data shapes

**Input file shape (`IWindParameters`)**, which is the parsed `.wind` JSON:

```ts
{
  layers: TLayerParameters[];          // wound in order
  mandrelParameters: { diameter, windLength };   // mm
  towParameters:     { width, thickness };       // mm (thickness is currently unused)
  defaultFeedRate: number;             // mm/min, emitted as "G0 F<rate>"
}
```

**Layer types.** The `windType` field is a *discriminated union*, so TypeScript knows which fields exist in each `case` of the planner's `switch`:

| `windType` | Type | Fields |
|---|---|---|
| `"hoop"` | `THoopLayer` | `terminal`: if true, wind one way and stop, with no layers allowed after it (used for heat-shrink tape) |
| `"helical"` | `THelicalLayer` | `windAngle`, `patternNumber`, `skipIndex`, `lockDegrees`, `leadInMM`, `leadOutDegrees`, `skipInitialNearLock` |
| `"skip"` | `TSkipLayer` | `mandrelRotation`: rotate the mandrel only (not in the README, but supported) |

`ILayerParameters<T>` bundles one layer's own parameters with the global mandrel and tow parameters, so each layer planner receives everything it needs in one object.

**Coordinate types:**

- `ECoordinateAxes` is a `const enum` with the values `'carriage' | 'mandrel' | 'deliveryHead'`. Because it is a `const enum`, the compiler inlines these strings.
- `TCoordinateAxes = Record<ECoordinateAxes, number>` is a *complete* position with all 3 axes.
- `AtLeastOne<T>` is a type trick. It produces "a partial `T`, but with at least one key present". `TCoordinate = AtLeastOne<TCoordinateAxes>` therefore means "a move that specifies 1, 2 or 3 axes". So `machine.move({ mandrel: 360 })` is legal, and `machine.move({})` is a compile error.
- `AxisLookup` maps logical axes to G-code letters: carriage→`X`, mandrel→`Y`, deliveryHead→`Z`. **This is the single place where the physical wiring is defined.**

### 2.2 `helpers.ts`

- `serializeCoordinate(c)` formats a coordinate as `{carriage mandrel head}` for verbose comments.
- `interpolateCoordinates(start, end, steps)` returns `steps` evenly spaced points from `start` to `end`, **including both endpoints** (the divisor is `steps - 1`). If `steps === 1` it returns just `[end]`. All three axes are interpolated linearly, so the mandrel angle and head angle change in proportion to the carriage position.

### 2.3 `machine.ts`: `WinderMachine`

This class is a virtual model of the winder. The planner tells it where to go, and it:
1. remembers where the machine currently is (`lastPosition`),
2. builds the list of G-code lines (`gcode[]`),
3. estimates run time and tow usage as it goes (the "profiler").

#### Public methods

| Method | G-code emitted | Notes |
|---|---|---|
| `addRawGCode(cmd)` | `cmd` verbatim | Does **not** update `lastPosition` |
| `insertComment(text)` | `; text` | `run` prints comments to the console as progress messages instead of sending them |
| `setFeedRate(f)` | `G0 F<f>` | Also stored for the time estimate |
| `setPosition(pos)` | `G92 X.. Y.. Z..` | *Redefines* the current position without moving, and updates `lastPosition` |
| `move(pos)` | one or more `G0 ...` | See below |
| `zeroAxes(angle)` | `G92`, `G0`, `G92` | See below |
| `getGCode()`, `getGCodeTimeS()`, `getTowLengthM()` | none | Results |
| `setMandrelDiameter(d)` | none | Exists for growing the diameter per layer, but **nothing calls it yet** |

#### `move()`: why moves are cut into ~1 mm pieces

```ts
const completeEndPosition = {...this.lastPosition, ...position};
```
First it builds the full target by taking the last position and overwriting whichever axes were given.

- **If the carriage does not move** (e.g. rotating only the mandrel, or only tilting the head), the move is emitted as **one** `G0`.
- **If the carriage moves**, the move is split into `round(|Δcarriage|) + 1` interpolated points, so each `G0` covers about **1 mm of carriage travel**.

The reason is in the comment on `moveSegment`: *Marlin only pauses after the current command finishes.* The `run` command pauses by sending `M0`, which Marlin only acts on between moves. A 940 mm pass sent as a single `G0` could not be paused until it ended. At 1 mm per command the machine stops almost immediately. The cost is large G-code files: every helical pass is about 940 lines.

(Side effect: because `interpolateCoordinates` includes the start point, the first `G0` of each segmented move is a zero-length move to the current position. It does no harm.)

#### `moveSegment()`: emitting a line and profiling it

For each axis in the move it:
1. appends `X<value>` / `Y<value>` / `Z<value>` to the `G0` line, rounded to 6 decimals by `stripPrecision` (Marlin rejects very long floats),
2. adds the squared axis delta to a **time** estimate. Marlin works out the feed rate from the Euclidean length over all axes and counts degrees as mm, so this does the same: `time += sqrt(Δx² + Δy² + Δz²) / feedRate * 60`. The estimate assumes infinite acceleration, so real runs take longer.
3. adds to a **tow length** estimate that uses only physical surface distance: `sqrt(Δx² + (Δy/360 · π·d)²)`. Delivery-head rotation is ignored because it does not pull more tow off the spool.

#### `zeroAxes(currentAngleDegrees)`: resetting between layers

```
G92 X0 Y<angle % 360> Z0     ; "you are at carriage 0, head 0, mandrel angle = angle mod 360"
G0 Y360                      ; rotate forward to the next full turn
G92 Y0                       ; call that 0
```
Every layer ends by calling this, so **every layer starts at (0, 0, 0)** and mandrel values stay small instead of growing into the hundreds of thousands of degrees. The comment says it "moves carriage and delivery head to 0", but it only *declares* them 0 with G92. That is correct only because every layer planner has already physically moved them to 0 before calling it. (If `angle % 360` is exactly 0, it makes one extra full turn, which does no harm.)

### 2.4 `planner.ts`: the winding logic

#### `planWind(params, verbose)`: the top level

1. Creates a `WinderMachine` for the mandrel diameter.
2. Emits the header comment `; Parameters {"mandrel":{...},"tow":{...}}`. **The plotter depends on this being line 1.**
3. Emits `G0 X0 Y0 Z0` (go home) and `G0 F<defaultFeedRate>`.
4. For each layer:
   - stops with a warning if a previous layer was a terminal hoop,
   - writes a `; Layer i of N: <type>` comment,
   - dispatches to `planHoopLayer`, `planHelicalLayer` or `planSkipLayer`,
   - prints that layer's time and tow estimates (the difference from the running totals).
5. Prints the totals and returns the list of G-code lines.

**The rule every layer planner must follow** (the comment above `planHoopLayer`): *make a complete there-and-back and leave the machine at (0,0,0) with the axes re-zeroed.* That is why layers can be stacked in any order.

---

#### `planHoopLayer`: hoop winding (≈90° to the axis)

A hoop layer lays tow side by side like thread on a spool, advancing **exactly one tow width per mandrel revolution** (the code comment calls this "overlap factor 1.0").

Key values:
```ts
lockDegrees       = 180                         // fixed dwell at each end
mandrelRotations  = windLength / tow.width      // e.g. 940 / 7 = 134.29 turns
windAngle         = 90 - atan(diameter / width) // used ONLY for the delivery-head tilt
```

Sequence (angles are cumulative mandrel degrees):

```
              carriage 0                                          carriage = windLength
near end  ●───────────────────────────────────────────────────────────● far end
 1. near lock: rotate 180°, head 0
 2. tilt head to -windAngle
 3. ──────────── wind: carriage 0 → L, mandrel += rotations·360 ────────▶
                                                     4. far lock: +180°, head → 0
                                    (terminal? stop here, carriage left at far end)
                                                     5. tilt head to +windAngle
 6. ◀─────────── wind back: carriage L → 0, mandrel += rotations·360 ───
 7. near lock: +180°, head → 0
 8. zeroAxes()
```

The "locks" are extra mandrel rotation with the carriage stopped. Their job is to anchor the tow at the ends so it does not slip when the direction of travel reverses.

With the README example (d = 69.75, L = 940, w = 7), one hoop pass is about 48,343° of mandrel rotation, and a full there-and-back puts **two** layers of hoop tow on the part.

---

#### `planHelicalLayer`: helical winding (the core of the planner)

A helical layer lays tow at an angle `windAngle` measured from the mandrel's axis (55° in the README). One **circuit** has a "there" pass (carriage 0 → L) at +θ and a "back" pass (L → 0) at −θ. Together the circuits build a balanced ±θ ply. Enough circuits are wound, each shifted around the mandrel, to cover the whole surface.

##### Step 1: derived quantities

Here is each formula with the README example worked through (d = 69.75, L = 940, tow w = 7, θ = 55°, leadIn = 30 mm, lock = 720°, leadOut = 90°, pattern = 2):

| Variable | Formula | Meaning | Example |
|---|---|---|---|
| `mandrelCircumference` | π·d | | 219.13 mm |
| `towArcLength` | w / cos θ | How wide one band of tow is **measured around the circumference** when laid at angle θ. A slanted band covers more of the circumference than its own width. | 12.20 mm |
| `numCircuits` | ceil(C / towArcLength) | How many side-by-side bands are needed to go all the way round. Rounded up, so bands overlap slightly. | ceil(17.96) = **18** |
| `patternStepDegrees` | 360 / numCircuits | Angular width of one band, i.e. the shift between neighbouring bands | 20° |
| `passRotationMM` | L · tan θ | How far the mandrel surface must travel while the carriage covers L to give angle θ (tan θ = circumferential / axial) | 1342.46 mm |
| `passRotationDegrees` | 360 · passRotationMM / C | The same, in mandrel degrees | 2205.51° (6.13 turns) |
| `passDegreesPerMM` | passRotationDegrees / L | Mandrel degrees per mm of carriage. This ratio is what fixes the wind angle. | 2.346 °/mm |
| `deliveryHeadAngleDegrees` | −(90 − θ) | Head tilt during the pass so the tow band lies flat | −35° |
| `deliveryHeadPassStartAngle` | −10 (hard-coded) | Head angle at the start/end of each pass | −10° |
| `leadInDegrees` | passDegreesPerMM · leadInMM | Mandrel rotation during the first `leadInMM` of the pass | 70.39° |
| `mainPassDegrees` | passDegreesPerMM · (L − leadInMM) | Mandrel rotation for the rest of the pass | 2135.12° |
| `numberOfPatterns` | numCircuits / patternNumber | How many times the pattern repeats | 9 |

**Validation:** if `numCircuits` is not divisible by `patternNumber`, the function prints a warning and **returns without winding anything** for that layer. Planning of the other layers carries on. This means a bad pattern number gives a G-code file that is silently missing a layer, so check the console output. The usual fix is to change `windAngle` slightly or use a different `patternNumber`.

##### Step 2: optional initial near lock

Unless `skipInitialNearLock` is true, the mandrel turns `lockDegrees` at carriage 0 and then G92-resets the mandrel to 0. This anchors the tow before the first pass. When several helical layers follow each other, the previous layer already finished with a lock, so this one can be skipped.

##### Step 3: the nested loops

```
for patternIndex in 0 .. numberOfPatterns-1:          // e.g. 9 times
    for inPatternIndex in 0 .. patternNumber-1:        // e.g. 2 start positions
        for pass in [there, back]:
            (a) rotate mandrel to mandrelPositionDegrees, head → 0      ← finishes the previous lock
            (b) tilt head to ±10°
            (c) LEAD-IN : carriage → leadInEnd,  mandrel += leadInDegrees,  head → ±35°
            (d) MAIN    : carriage → pass end,   mandrel += mainPassDegrees
            (e) LEAD-OUT: mandrel += leadOutDegrees, head → ±10°            (carriage stopped)
            mandrelPositionDegrees += lockDegrees − leadOutDegrees − (passRotationDegrees mod 360)
        mandrelPositionDegrees += 360 / patternNumber      // jump to next start position
    mandrelPositionDegrees += patternStepDegrees           // shift the whole pattern by one band
final lock (+lockDegrees, head → 0), then zeroAxes()
```

`deliveryHeadSign` is +1 for the "there" pass and −1 for the "back" pass, so the head tilts one way going out and the opposite way coming back.

Lead-in: during the first `leadInMM` of carriage travel the head swings from ±10° to ±35°, so the head does not snap to full angle at the start. Lead-out: during the first `leadOutDegrees` of the end lock it swings back to ±10°, and during the rest of the lock (step (a) of the *next* pass) it returns to 0.

##### Step 4: the lock arithmetic

This line is the least obvious one in the planner:

```ts
mandrelPositionDegrees += lockDegrees - leadOutDegrees - (passRotationDegrees % 360);
```

Add up one whole pass:

```
leadIn + main + leadOut + (lock − leadOut − passRot mod 360)
= passRot + lock − (passRot mod 360)
= 360·floor(passRot/360) + lock
```

So each pass, lock included, turns the mandrel by **a whole number of turns plus `lockDegrees`**. The actual dwell at the end is `lockDegrees − (passRot mod 360)`, which is shortened so that the next pass starts at a predictable angle. With `lockDegrees = 720` (a multiple of 360), **every pass starts at the same angle, mod 360, as the previous one.**

Example trace (mandrel angle mod 360 at the start of each pass):

```
Pattern 1, circuit 1: there @ 0°,   back @ 0°      then += 360/2 = 180
Pattern 1, circuit 2: there @ 180°, back @ 180°    then += 180 → 0, then += 20 (patternStep)
Pattern 2, circuit 1: there @ 20°,  back @ 20°
Pattern 2, circuit 2: there @ 200°, back @ 200°
...
Pattern 9, circuit 2: there @ 340°, back @ 340°     → 18 start positions, 20° apart = full coverage
```

The "jump to next start position" and "shift by patternStep" additions have no `move()` of their own. They are folded into step (a) of the next pass, so they become extra rotation during the near-end lock.

##### About `patternNumber` and `skipIndex`

- `patternNumber` sets how many start positions are spread evenly around the mandrel within one pattern (2 means 180° apart). It changes the look of the finished part, i.e. how many diamond-shaped crossover regions it has.
- **`skipIndex` is read from the file but never used.** The code always visits the start positions in order (0, 1, 2, …), which matches `skipIndex = 1`. Setting any other value has no effect.

---

#### `planSkipLayer`

This rotates the mandrel by `mandrelRotation` degrees with the carriage and head at 0, then G92-resets the mandrel to 0. Use it to offset where the next layer starts. Note that it does *not* call `zeroAxes()`.

---

## 3. `src/plotter/`: G-code → PNG

`plotGCode(lines)`:
1. Reads line 1. It must be `; Parameters {json}` (written by `planWind`), which supplies the mandrel and tow sizes. If it is missing, the plotter gives up.
2. Creates a canvas `windLength` px wide × 360 px tall. This is the mandrel surface **unrolled**: x = carriage mm, y = mandrel degrees.
3. For every `G0` line it reads the new X/Y (Z is ignored) and draws the segment as a purple line `tow.width` wide with a yellow line at 75% of that width on top. Comments are skipped, and any other command is logged and skipped.

`generateCoordinates(start, end)` in `plotter/helpers.ts` handles wrap-around. The mandrel angle keeps growing (e.g. 2205°) but the image is only 360 px tall, so a segment that crosses a multiple of 360° is split recursively at the crossing point. Each piece is drawn mod 360, the way a stripe on a barber pole wraps around.

---

## 4. `src/marlin-port.ts`: streaming to the machine

`MarlinPort` wraps a `serialport` connection (115200 baud) and implements **simple ping-pong flow control**:

- `queueCommand(line)` adds the line to `commandQueue` and calls `tryNextCommand()`.
- `tryNextCommand()` sends the next line only if no command is waiting for a reply (`hasCommandWaiting`), the queue is not empty, and the machine is not paused. Lines starting with `;` are **printed to the console instead of sent**. That is how the `; Layer 2 of 5: helical` and `; Pattern: 3/9 Circuit: 1/2` comments show up as progress messages.
- `processSerialResponseLine()` handles each line Marlin sends back:
  - `ok` means the command finished, so it sends the next one.
  - `echo:busy: ...` is a keep-alive and is ignored.
  - `//action:notification Click to Resume...` means Marlin has actually paused (after `M0`).
  - `//action:notification 3D Printer Ready.` means Marlin has resumed (after `M108`).
  - anything else is logged as unexpected.

**Pause and resume** (spacebar, set up in `cli-entry.ts`):

```
running ──space──▶ pausing ──(Marlin: "Click to Resume")──▶ paused ──space──▶ resuming ──("Printer Ready")──▶ running
          sends M0 (runs after current ~1mm move)                    sends M108
```

`M0` and `M108` are written directly and skip the queue. This is the reason `machine.ts` cuts moves into 1 mm segments: Marlin acts on `M0` only once the current move has finished.

---

## 5. `src/cli-entry.ts` and `src/helpers.ts`

- `cli-entry.ts` uses `yargs` to set up the `run`, `plan` and `plot` commands described in section 1. `plan` reads the `.wind` file, `JSON.parse`s it with no validation (there is a `// Todo`), calls `planWind`, and writes the lines joined with `\n`.
- `helpers.ts` holds `isObject`, `degToRad`, `radToDeg`, and `stripPrecision` (rounds to 6 decimals so Marlin can parse the numbers).

---

## 6. Things worth knowing / possible issues

These are observations from reading the code. None of them has been tested on hardware.

1. **`skipIndex` does nothing** (section 2.4). The start positions are always visited in order.
2. **The comment on `windAngle` in `types.ts` is misleading.** It calls it "the complement of the angle between the mandrel axis and the tow", but the math (`tan θ`, `w / cos θ`) treats it as the angle **from** the mandrel axis, which is the standard definition. 0° would be along the axis and 90° would be a hoop.
3. **Hoop head tilt:** `90 − atan(d / w)` has no π in it. The true helix angle of a hoop wind is `atan(w / (π·d))`, which is 1.83° from pure hoop in the example, while the code tilts the head 5.73°. This only affects the delivery-head tilt, not where the tow is laid (the carriage and mandrel ratio is correct). It may be deliberate or tuned by hand, but check it.
4. **The mandrel diameter never grows.** Every layer is planned with the bare mandrel diameter, even though each layer makes the part thicker. `setMandrelDiameter()` and `tow.thickness` exist for this but are not used yet (see the "Increment mandrel diameter, etc" comment in `planWind`).
5. **A helical layer with bad parameters is silently dropped** (it warns and returns), and the G-code is still written.
6. **Only hoop layers can be `terminal`.** A terminal hoop leaves the carriage at the far end without calling `zeroAxes()`, which is why no layer may follow it.
7. **`plan` without `-o` crashes**, because `output` is not marked required but is passed straight to `writeFile`.
8. The initial `G0 X0 Y0 Z0` is emitted **before** the feed rate is set, so it runs at whatever feed rate Marlin was last using.
9. Time estimates ignore acceleration, so real runs will take longer.

---

## 7. Relevance to the README to-do: "work with only 2 axes"

If the SSTA machine has no rotating delivery head (only carriage + mandrel), the parts that touch the head are:

- `types.ts`: the `DELIVERY_HEAD` entry in `ECoordinateAxes` and the `Z` entry in `AxisLookup`.
- `helpers.ts`: `serializeCoordinate` and `interpolateCoordinates` both reference the head axis.
- `machine.ts`: `zeroAxes()` sets `DELIVERY_HEAD: 0`, and `moveSegment` has a `DELIVERY_HEAD` case in its switch.
- `planner.ts`: every `[ECoordinateAxes.DELIVERY_HEAD]: ...` in the hoop and helical planners, plus the head-only "tilt" moves (hoop steps 2 and 5, helical step (b)). Once the head entry is removed, those turn into empty moves, which the `AtLeastOne` type will reject at compile time and so point you to them.
- `planWind`: the raw `G0 X0 Y0 Z0`.

The winding math itself (circuits, pattern stepping, lock arithmetic, 1 mm segmentation) does not depend on the delivery head and can stay as it is. The head only improves how flat the tow band lies. Without it, expect the band to twist or narrow a little at the turnarounds.
