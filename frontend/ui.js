/* Small shared interface behaviours. All assets are local and use the theme colour. */
(() => {
  "use strict";
  // The supplied animation inspired this compact vector balloon and moving tether.
  // Vector artwork stays sharp at small sizes; no video download is required.
  const balloon = `<svg class="balloon-loader-art" viewBox="0 0 56 74" focusable="false" aria-hidden="true">
    <g class="loader-clouds" fill="none" stroke="currentColor" stroke-width="1.6" opacity=".3">
      <path class="loader-cloud cloud-one" d="M2 21h14a4 4 0 0 0 0-8 6 6 0 0 0-11-1 4 4 0 0 0-3 9Z"/>
      <path class="loader-cloud cloud-two" d="M39 56h13a3 3 0 0 0 0-6 5 5 0 0 0-9-1 4 4 0 0 0-4 7Z"/>
    </g>
    <g class="loader-balloon" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
      <path d="M28 8C17 8 12 15 14 24c1 7 9 15 12 19h4c3-4 11-12 12-19C44 15 39 8 28 8Z" stroke-width="2.8"/>
      <path d="M22 10c-6 10-2 21 6 33m6-33c6 10 2 21-6 33M25 47h6" stroke-width="2"/>
      <path class="loader-tether" d="M28 51c-4 6 5 11 0 19" stroke-width="1.8"/>
    </g>
  </svg>`;
  for (const node of document.querySelectorAll("[data-balloon-loader]")) node.innerHTML=balloon;
  for (const details of document.querySelectorAll("details")) {
    const summary=details.querySelector(":scope > summary");if(!summary)continue;
    let indicator=summary.querySelector(".disclosure-indicator");
    if(!indicator){indicator=summary.querySelector("span")||document.createElement("span");indicator.className="disclosure-indicator";summary.append(indicator);}
    indicator.setAttribute("aria-hidden","true");
    const update=()=>{indicator.textContent=details.open?"−":"+";};
    details.addEventListener("toggle",update);update();
  }
  window.BalloonUI={predictionBusy(busy){
    document.getElementById("workspace").classList.toggle("prediction-busy",busy);
    document.getElementById("flight-form").setAttribute("aria-busy",String(busy));
    for(const node of document.querySelectorAll("[data-balloon-loader=flight]"))node.hidden=!busy;
  }};
})();
