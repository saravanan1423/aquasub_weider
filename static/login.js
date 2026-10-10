(() => {
  const password = document.getElementById("password");
  const passwordToggle = document.getElementById("toggle-password");
  if (password && passwordToggle) {
    passwordToggle.addEventListener("mousedown", event => event.preventDefault());
    passwordToggle.addEventListener("click", () => {
      const visible = password.type === "password";
      password.type = visible ? "text" : "password";
      passwordToggle.classList.toggle("password-visible", visible);
      passwordToggle.setAttribute("aria-pressed", String(visible));
      passwordToggle.setAttribute("aria-label", visible ? "Hide password" : "Show password");
      passwordToggle.title = visible ? "Hide password" : "Show password";
      password.dispatchEvent(new Event("input", {bubbles: true}));
    });
  }
  const field = document.querySelector(".login-username-field");
  const input = document.getElementById("username");
  const suggestions = document.getElementById("username-suggestions");
  if (!field || !input || !suggestions) return;

  const options = [...suggestions.querySelectorAll("button")];
  let activeIndex = -1;

  function close() {
    suggestions.hidden = true;
    input.setAttribute("aria-expanded", "false");
    options.forEach(option => option.classList.remove("active"));
    activeIndex = -1;
  }

  function visibleOptions() {
    return options.filter(option => !option.hidden);
  }

  function update() {
    const query = input.value.trim().toLocaleLowerCase();
    options.forEach(option => {
      option.hidden = !query || !option.dataset.username.toLocaleLowerCase().includes(query);
      option.classList.remove("active");
    });
    activeIndex = -1;
    suggestions.hidden = !visibleOptions().length;
    input.setAttribute("aria-expanded", String(!suggestions.hidden));
  }

  function choose(option) {
    input.value = option.dataset.username;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    close();
    input.focus();
  }

  input.addEventListener("input", update);
  input.addEventListener("keydown", event => {
    if (suggestions.hidden) return;
    const visible = visibleOptions();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
      visible.forEach((option, index) => option.classList.toggle("active", index === activeIndex));
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      choose(visible[activeIndex]);
    } else if (event.key === "Escape") {
      close();
    }
  });

  suggestions.addEventListener("mousedown", event => event.preventDefault());
  suggestions.addEventListener("click", event => {
    const option = event.target.closest("button");
    if (option) choose(option);
  });
  document.addEventListener("click", event => {
    if (!field.contains(event.target)) close();
  });
})();
