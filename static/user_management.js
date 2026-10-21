const $ = id => document.getElementById(id);
const screenOptions = [
  ["main", "Main Screen"],
  ["allocation", "Melt Material Allocation"],
  ["settings", "Settings"],
  ["communication", "Communication"],
  ["shifts", "Shift Settings"],
  ["melts", "Melt Number Settings"],
  ["backup", "Backup Settings"],
  ["products", "Product Master"],
  ["logs", "Capture Logs"]
];
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function message(text, error = false) {
  const box = $("user-message");
  box.hidden = !text;
  box.className = `form-message ${error ? "form-error" : "form-success"}`;
  box.textContent = text;
}
async function loadUsers() {
  const users = await api("/api/users");
  $("users-count").textContent = users.length;
  $("users-list").innerHTML = users.map(user => `
    <article class="user-row" data-id="${user.id}">
      <div class="user-identity">
        <span>${escapeHtml(user.username.charAt(0).toUpperCase())}</span>
        <div>
          <input class="user-name" value="${escapeHtml(user.username)}" ${user.role === 'admin' ? 'disabled' : ''}>
          <small>${user.role === 'admin' ? 'Administrator · full access' : 'Standard user'}</small>
        </div>
      </div>
      ${user.role === 'admin' ? '<div class="admin-access">All screens</div>' : `
        <div class="user-permissions">
          ${screenOptions.map(([key, label]) => `
            <label><input type="checkbox" value="${key}" ${user.permissions.includes(key) ? 'checked' : ''}> ${label}</label>
          `).join('')}
        </div>
        <input class="new-user-password" type="password" placeholder="New password (optional)" minlength="6">
        <div class="user-actions">
          <button class="save-user" type="button">Save</button>
          <button class="delete-user" type="button">Delete</button>
        </div>
      `}
    </article>
  `).join("");

  users.filter(user => user.role !== 'admin').forEach(user => {
    const row = document.querySelector(`.user-row[data-id="${user.id}"]`);
    const container = row.querySelector('.user-identity > div');

    const defLabel = document.createElement('label');
    defLabel.className = 'default-screen-label';
    defLabel.innerHTML = `<small>Main screen: </small><select class="user-default-screen">
      <option value="main" ${user.default_screen !== 'allocation' ? 'selected' : ''}>Main Screen</option>
      <option value="allocation" ${user.default_screen === 'allocation' ? 'selected' : ''}>Melt Material Allocation</option>
    </select>`;
    container.appendChild(defLabel);

    const loginLabel = document.createElement('label');
    loginLabel.className = 'login-visibility';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'show-in-login';
    checkbox.checked = user.show_in_login;
    loginLabel.append(checkbox, ' Show in login dropdown');
    container.appendChild(loginLabel);

    const permInputs = row.querySelectorAll('.user-permissions input');
    const select = defLabel.querySelector('.user-default-screen');
    permInputs.forEach(input => {
      input.addEventListener('change', () => {
        const hasMain = row.querySelector('.user-permissions input[value="main"]')?.checked;
        const hasAlloc = row.querySelector('.user-permissions input[value="allocation"]')?.checked;
        if (hasAlloc && !hasMain) select.value = 'allocation';
        else if (hasMain && !hasAlloc) select.value = 'main';
      });
    });
  });

  document.querySelectorAll('.save-user').forEach(button => button.addEventListener('click', () => saveUser(button.closest('.user-row'))));
  document.querySelectorAll('.delete-user').forEach(button => button.addEventListener('click', () => deleteUser(button.closest('.user-row'))));
}

async function saveUser(row) {
  try {
    await api(`/api/users/${row.dataset.id}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        username: row.querySelector('.user-name').value,
        password: row.querySelector('.new-user-password').value,
        show_in_login: row.querySelector('.show-in-login').checked,
        default_screen: row.querySelector('.user-default-screen')?.value || 'main',
        permissions: [...row.querySelectorAll('.user-permissions input:checked')].map(item => item.value)
      })
    });
    message('User updated successfully');
    await loadUsers();
  } catch (error) {
    message(error.message, true);
  }
}

async function deleteUser(row) {
  const name = row.querySelector('.user-name').value;
  if (!confirm(`Delete user “${name}”?`)) return;
  try {
    await api(`/api/users/${row.dataset.id}`, {method: 'DELETE'});
    message('User deleted successfully');
    await loadUsers();
  } catch (error) {
    message(error.message, true);
  }
}

$("create-user-form")?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api('/api/users', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        username: $("new-username").value,
        password: $("new-password").value,
        show_in_login: $("show-in-login").checked,
        default_screen: $("new-default-screen")?.value || 'main',
        permissions: [...form.querySelectorAll('fieldset input:checked')].map(item => item.value)
      })
    });
    form.reset();
    message('User created successfully');
    await loadUsers();
  } catch (error) {
    message(error.message, true);
  }
});

$("create-user-form")?.querySelectorAll('fieldset input').forEach(input => {
  input.addEventListener('change', () => {
    const hasMain = $("create-user-form").querySelector('input[value="main"]')?.checked;
    const hasAlloc = $("create-user-form").querySelector('input[value="allocation"]')?.checked;
    const select = $("new-default-screen");
    if (select) {
      if (hasAlloc && !hasMain) select.value = 'allocation';
      else if (hasMain && !hasAlloc) select.value = 'main';
    }
  });
});

$("change-password-button").addEventListener('click', async () => {
  const box = $("password-message");
  box.hidden = true;
  try {
    const result = await api('/api/change-password', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        current_password: $("current-admin-password").value,
        new_password: $("new-admin-password").value,
        confirm_password: $("confirm-admin-password").value
      })
    });
    $("current-admin-password").value = $("new-admin-password").value = $("confirm-admin-password").value = '';
    box.className = 'form-message form-success';
    box.textContent = result.message;
    box.hidden = false;
  } catch (error) {
    box.className = 'form-message form-error';
    box.textContent = error.message;
    box.hidden = false;
  }
});

loadUsers().catch(error => message(error.message, true));
