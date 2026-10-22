# Lens: videogames
- Short GDD: core loop, controls, win/lose, juice, session length. Prototype first (Phaser 4 web for
  jams/prototypes — official skills in the Phaser repo; Godot for releases — GodotPrompter +
  godot-mcp for agent playtests). Unity only for mobile F2P with ads/IAP.
- Art bible (written before any asset): base resolution + integer scale, camera/perspective per
  scene, sprite size chart with a scale reference, palette + ramps, light direction, outline/anti-alias rules,
  animation frame counts/fps, the AI-generation + cleanup pipeline. Order: bible → key scene → kit → batch.
  Characters: base body + anchored part sets (eyes, hair, mouth, clothes) carrying the base's frames. Scene
  basics before the user looks: facing, camera distance, legibility, no dev toggles, seen on a phone viewport.
- Playtest with `budgit:persona-sim` on the running build; fun is a hypothesis until real players.
