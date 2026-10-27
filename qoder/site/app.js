(() => {
  "use strict";

  const tabsRoot = document.querySelector("[data-tabs]");
  if (tabsRoot) {
    const tabs = Array.from(tabsRoot.querySelectorAll('[role="tab"]'));
    const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));

    const select = (index) => {
      tabs.forEach((tab, i) => {
        const on = i === index;
        tab.classList.toggle("active", on);
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
        if (panels[i]) panels[i].classList.toggle("active", on);
      });
    };

    tabs.forEach((tab, i) => {
      tab.tabIndex = i === 0 ? 0 : -1;
      tab.addEventListener("click", () => select(i));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const next = (i + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
        select(next);
        tabs[next].focus();
      });
    });
  }

  document.querySelectorAll(".copy").forEach((button) => {
    button.addEventListener("click", async () => {
      const block = button.parentElement;
      const code = block && block.querySelector("code");
      if (!code) return;
      const text = code.textContent.replace(/\n+$/, "");
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "已复制";
        button.classList.add("done");
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = "已选中";
      }
      window.setTimeout(() => {
        button.textContent = "复制";
        button.classList.remove("done");
      }, 1800);
    });
  });

  const lightbox = document.getElementById("lightbox");
  if (lightbox) {
    const image = lightbox.querySelector("img");
    const close = lightbox.querySelector(".lightbox-close");

    const hide = () => {
      lightbox.hidden = true;
      image.removeAttribute("src");
      document.body.style.overflow = "";
    };

    document.querySelectorAll(".shot-btn").forEach((button) => {
      button.addEventListener("click", () => {
        const src = button.getAttribute("data-full");
        if (!src) return;
        image.src = src;
        image.alt = button.querySelector("img")?.alt || "";
        lightbox.hidden = false;
        document.body.style.overflow = "hidden";
        close.focus();
      });
    });

    close.addEventListener("click", hide);
    image.addEventListener("click", hide);
    lightbox.addEventListener("click", (event) => {
      if (event.target === lightbox) hide();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !lightbox.hidden) hide();
    });
  }

  const sections = Array.from(document.querySelectorAll("section[id]"));
  const navLinks = Array.from(document.querySelectorAll(".nav a"));
  if ("IntersectionObserver" in window && sections.length && navLinks.length) {
    const byId = new Map(navLinks.map((link) => [link.getAttribute("href").slice(1), link]));
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          navLinks.forEach((link) => link.removeAttribute("aria-current"));
          const link = byId.get(entry.target.id);
          if (link) link.setAttribute("aria-current", "true");
        });
      },
      { rootMargin: "-45% 0px -50% 0px" }
    );
    sections.forEach((section) => observer.observe(section));
  }

})();
