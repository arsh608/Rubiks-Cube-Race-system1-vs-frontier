/* Cube rendering: CSS-3D cube (U/F/R visible, drag to rotate) and a flat 2D net.
 * Facelet string order is URFDLB, 9 per face, row-major — identical to the backend engine.
 * The standard CSS-cube face transforms below read each face exactly in that order. */
(function () {
  "use strict";
  const FACES = ["U", "R", "F", "D", "L", "B"];
  const COLOURS = { U: "#f4f4f4", R: "#d62828", F: "#2a9d4b", D: "#ffd60a", L: "#ff7b00", B: "#1d5fd6" };
  const FACE_CLASS = { U: "f-top", R: "f-right", F: "f-front", D: "f-bottom", L: "f-left", B: "f-back" };

  function faceSlice(state, face) {
    const i = FACES.indexOf(face);
    return state.slice(i * 9, i * 9 + 9);
  }

  class Cube3D {
    constructor(container, opts = {}) {
      this.container = container;
      this.rx = opts.rx ?? -28;
      this.ry = opts.ry ?? -38;
      container.classList.add("cube3d-wrap");
      this.scene = document.createElement("div");
      this.scene.className = "cube3d-scene";
      this.cube = document.createElement("div");
      this.cube.className = "cube3d";
      this.stickers = {};
      for (const f of FACES) {
        const face = document.createElement("div");
        face.className = "cface " + FACE_CLASS[f];
        this.stickers[f] = [];
        for (let k = 0; k < 9; k++) {
          const s = document.createElement("div");
          s.className = "st";
          face.appendChild(s);
          this.stickers[f].push(s);
        }
        this.cube.appendChild(face);
      }
      this.scene.appendChild(this.cube);
      container.appendChild(this.scene);
      this.applyRotation();
      if (opts.draggable !== false) this.enableDrag();
    }

    applyRotation() {
      this.cube.style.transform = `rotateX(${this.rx}deg) rotateY(${this.ry}deg)`;
    }

    enableDrag() {
      let sx = 0, sy = 0, srx = 0, sry = 0, dragging = false;
      const down = (e) => {
        dragging = true;
        const p = e.touches ? e.touches[0] : e;
        sx = p.clientX; sy = p.clientY; srx = this.rx; sry = this.ry;
        this.container.classList.add("dragging");
      };
      const move = (e) => {
        if (!dragging) return;
        const p = e.touches ? e.touches[0] : e;
        this.ry = sry + (p.clientX - sx) * 0.6;
        this.rx = Math.max(-89, Math.min(89, srx - (p.clientY - sy) * 0.6));
        this.applyRotation();
        if (e.cancelable) e.preventDefault();
      };
      const up = () => { dragging = false; this.container.classList.remove("dragging"); };
      this.container.addEventListener("mousedown", down);
      this.container.addEventListener("touchstart", down, { passive: true });
      window.addEventListener("mousemove", move);
      window.addEventListener("touchmove", move, { passive: false });
      window.addEventListener("mouseup", up);
      window.addEventListener("touchend", up);
      this.container.addEventListener("dblclick", () => { this.rx = -28; this.ry = -38; this.applyRotation(); });
    }

    setState(state, highlightFace) {
      if (!state || state.length !== 54) return;
      for (const f of FACES) {
        const sl = faceSlice(state, f);
        for (let k = 0; k < 9; k++) this.stickers[f][k].style.background = COLOURS[sl[k]] || "#333";
      }
      if (highlightFace) {
        this.container.classList.remove("pulse");
        void this.container.offsetWidth; // restart animation
        this.container.classList.add("pulse");
      }
    }
  }

  // Net layout: 4 columns x 3 rows of faces.
  //      U
  //    L F R B
  //      D
  const NET_POS = { U: [1, 0], L: [0, 1], F: [1, 1], R: [2, 1], B: [3, 1], D: [1, 2] };

  class CubeNet {
    constructor(container) {
      this.container = container;
      container.classList.add("cube-net");
      this.faces = {};
      this.cells = {};
      for (const f of FACES) {
        const el = document.createElement("div");
        el.className = "net-face";
        el.style.gridColumn = String(NET_POS[f][0] + 1);
        el.style.gridRow = String(NET_POS[f][1] + 1);
        el.dataset.face = f;
        this.cells[f] = [];
        for (let k = 0; k < 9; k++) {
          const c = document.createElement("div");
          c.className = "net-st";
          el.appendChild(c);
          this.cells[f].push(c);
        }
        container.appendChild(el);
        this.faces[f] = el;
      }
    }

    setState(state, highlightFace) {
      if (!state || state.length !== 54) return;
      for (const f of FACES) {
        const sl = faceSlice(state, f);
        for (let k = 0; k < 9; k++) this.cells[f][k].style.background = COLOURS[sl[k]] || "#333";
        this.faces[f].classList.remove("hl");
      }
      if (highlightFace && this.faces[highlightFace]) {
        const el = this.faces[highlightFace];
        void el.offsetWidth;
        el.classList.add("hl");
      }
    }
  }

  window.CubeView = { Cube3D, CubeNet, COLOURS, FACES };
})();
