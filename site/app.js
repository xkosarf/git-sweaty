const COLORS = ["#ebedf0", "#c6e48b", "#7bc96f", "#239a3b", "#196127"];

const typeSelect = document.getElementById("typeSelect");
const yearSelect = document.getElementById("yearSelect");
const heatmap = document.getElementById("heatmap");
const tooltip = document.getElementById("tooltip");
const legend = document.getElementById("legend");

function level(count, maxCount) {
  if (count <= 0 || maxCount <= 0) return 0;
  if (maxCount === 1) return 1;
  const ratio = count / maxCount;
  let lvl = Math.floor(ratio * 3) + 1;
  if (lvl > 4) lvl = 4;
  return lvl;
}

function mondayOnOrBefore(d) {
  const day = d.getDay();
  const offset = (day + 6) % 7; // convert Sunday=0 to 6, Monday=1 to 0
  const result = new Date(d);
  result.setDate(d.getDate() - offset);
  return result;
}

function sundayOnOrAfter(d) {
  const day = d.getDay();
  const offset = (7 - day) % 7;
  const result = new Date(d);
  result.setDate(d.getDate() + offset);
  return result;
}

function buildLegend() {
  legend.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = "Less";
  legend.appendChild(label);
  COLORS.forEach((color) => {
    const swatch = document.createElement("span");
    swatch.style.background = color;
    legend.appendChild(swatch);
  });
  const more = document.createElement("span");
  more.textContent = "More";
  legend.appendChild(more);
}

function showTooltip(text, x, y) {
  tooltip.textContent = text;
  tooltip.style.left = `${x + 12}px`;
  tooltip.style.top = `${y + 12}px`;
  tooltip.classList.add("visible");
}

function hideTooltip() {
  tooltip.classList.remove("visible");
}

function renderGrid(aggregates, year, units) {
  heatmap.innerHTML = "";

  const yearStart = new Date(year, 0, 1);
  const yearEnd = new Date(year, 11, 31);
  const start = mondayOnOrBefore(yearStart);
  const end = sundayOnOrAfter(yearEnd);

  const entries = Object.values(aggregates || {});
  const maxCount = entries.reduce((max, entry) => Math.max(max, entry.count || 0), 0);

  for (let day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) {
    const dateStr = day.toISOString().slice(0, 10);
    const inYear = day.getFullYear() === year;
    const entry = (aggregates && aggregates[dateStr]) || {
      count: 0,
      distance: 0,
      moving_time: 0,
      elevation_gain: 0,
      activity_ids: [],
    };

    const weekIndex = Math.floor((day - start) / (1000 * 60 * 60 * 24 * 7));
    const row = (day.getDay() + 6) % 7; // Monday=0

    const cell = document.createElement("div");
    cell.className = "cell";
    cell.style.gridColumn = weekIndex + 1;
    cell.style.gridRow = row + 1;

    if (!inYear) {
      cell.classList.add("outside");
      heatmap.appendChild(cell);
      continue;
    }

    const lvl = level(entry.count || 0, maxCount);
    cell.style.background = COLORS[lvl];

    const distance = units.distance === "km"
      ? `${(entry.distance / 1000).toFixed(2)} km`
      : `${(entry.distance / 1609.344).toFixed(2)} mi`;
    const elevation = units.elevation === "m"
      ? `${Math.round(entry.elevation_gain)} m`
      : `${Math.round(entry.elevation_gain * 3.28084)} ft`;
    const durationMinutes = Math.round((entry.moving_time || 0) / 60);
    const duration = durationMinutes >= 60
      ? `${Math.floor(durationMinutes / 60)}h ${durationMinutes % 60}m`
      : `${durationMinutes}m`;

    const tooltipText = `${dateStr}\n${entry.count} workout${entry.count === 1 ? "" : "s"}\nDistance: ${distance}\nDuration: ${duration}\nElevation: ${elevation}`;

    cell.addEventListener("mouseenter", (event) => {
      showTooltip(tooltipText, event.clientX, event.clientY);
    });
    cell.addEventListener("mousemove", (event) => {
      showTooltip(tooltipText, event.clientX, event.clientY);
    });
    cell.addEventListener("mouseleave", hideTooltip);

    heatmap.appendChild(cell);
  }
}

async function init() {
  const resp = await fetch("data.json");
  const payload = await resp.json();

  payload.types.forEach((type) => {
    const opt = document.createElement("option");
    opt.value = type;
    opt.textContent = type;
    typeSelect.appendChild(opt);
  });

  payload.years.slice().reverse().forEach((year) => {
    const opt = document.createElement("option");
    opt.value = year;
    opt.textContent = year;
    yearSelect.appendChild(opt);
  });

  function update() {
    const type = typeSelect.value;
    const year = Number(yearSelect.value);
    const aggregates = payload.aggregates?.[String(year)]?.[type] || {};
    renderGrid(aggregates, year, payload.units || { distance: "mi", elevation: "ft" });
  }

  typeSelect.addEventListener("change", update);
  yearSelect.addEventListener("change", update);

  buildLegend();

  typeSelect.value = payload.types[0] || "";
  yearSelect.value = String(payload.years[payload.years.length - 1] || "");
  update();
}

init().catch((error) => {
  console.error(error);
});
