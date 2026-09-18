"use strict";

const language = document.querySelector("#language");
const topic = document.querySelector("#topic");
const all = document.querySelector("#all");
const sections = [...document.querySelectorAll("main > section")];
const topics = [...sections[0].querySelectorAll("article")].map(a => a.dataset.topic);
let state;
let focusHeading = false;

function readRoute() {
  const [lang, chapter, point, mode] = location.hash.slice(1).split("/");
  return {
    lang: lang === "en" ? "en" : "ko",
    topic: topics.includes(chapter) ? chapter : topics[0],
    point: /^[1-9]\d*$/.test(point || "") ? Number(point) : 1,
    all: mode !== "single"
  };
}

function navigate(changes) {
  const next = { ...state, ...changes };
  const hash = `#${next.lang}/${next.topic}/${next.point}${next.all ? "" : "/single"}`;
  if (location.hash === hash) render();
  else location.hash = hash;
}

function highlight(article, number) {
  const markers = [...article.querySelectorAll(".hotspot")];
  const active = markers[number - 1] || markers[0];
  for (const marker of markers) {
    if (marker === active) marker.setAttribute("aria-current", "step");
    else marker.removeAttribute("aria-current");
  }
  if (!active) return;
  const box = article.querySelector(".highlight");
  const [left, top, width, height] = active.dataset.box.split(",");
  Object.assign(box.style, { left: left + "%", top: top + "%", width: width + "%", height: height + "%" });
  box.hidden = false;
  article.querySelectorAll(".point").forEach(point => {
    point.classList.toggle("selected", point.id === active.getAttribute("href").slice(1));
  });
}

function render() {
  state = readRoute();
  document.documentElement.lang = state.lang;
  for (const element of document.querySelectorAll("[data-ko][data-en]")) {
    element.textContent = element.dataset[state.lang];
  }
  document.title = document.querySelector("h1").textContent;
  language.value = state.lang;
  all.checked = state.all;
  for (const section of sections) {
    section.hidden = section.lang !== state.lang;
    for (const article of section.querySelectorAll("article")) {
      article.hidden = !state.all && article.dataset.topic !== state.topic;
      highlight(article, article.dataset.topic === state.topic ? state.point : 1);
    }
  }
  const currentSection = document.getElementById(state.lang);
  topic.replaceChildren(...[...currentSection.querySelectorAll("article")].map(a => new Option(a.querySelector("h2").textContent, a.dataset.topic)));
  topic.value = state.topic;
  const index = topics.indexOf(state.topic);
  document.querySelector("#progress").textContent = `${index + 1} / ${topics.length}`;
  document.querySelector("#prev").disabled = index === 0;
  document.querySelector("#next").disabled = index === topics.length - 1;
  if (focusHeading) {
    const heading = document.getElementById(`${state.lang}-${state.topic}`).querySelector("h2");
    heading.focus();
    heading.scrollIntoView({ block: "nearest" });
    focusHeading = false;
  }
}

language.addEventListener("change", () => navigate({ lang: language.value }));
topic.addEventListener("change", () => { focusHeading = true; navigate({ topic: topic.value, point: 1 }); });
all.addEventListener("change", () => navigate({ all: all.checked }));
for (const [id, direction] of [["prev", -1], ["next", 1]]) {
  document.getElementById(id).addEventListener("click", () => {
    focusHeading = true;
    navigate({ topic: topics[topics.indexOf(state.topic) + direction], point: 1 });
  });
}
for (const marker of document.querySelectorAll(".hotspot")) {
  marker.addEventListener("click", event => {
    event.preventDefault();
    const article = marker.closest("article");
    const detail = document.getElementById(marker.getAttribute("href").slice(1));
    navigate({ topic: article.dataset.topic, point: Number(marker.dataset.point) });
    detail.querySelector("button").focus({ preventScroll: true });
    detail.scrollIntoView({ block: "nearest" });
  });
}
for (const button of document.querySelectorAll(".point-select")) {
  button.addEventListener("click", () => {
    navigate({ topic: button.closest("article").dataset.topic, point: Number(button.dataset.point) });
  });
}
for (const link of document.querySelectorAll("[data-topic-link]")) {
  link.addEventListener("click", event => {
    event.preventDefault();
    focusHeading = true;
    navigate({ topic: link.dataset.topicLink, point: 1 });
  });
}

document.querySelector("#print").addEventListener("click", () => window.print());
window.addEventListener("hashchange", render);
render();
document.querySelector("#tools").hidden = false;
document.querySelector("#navigation").hidden = false;
