const loginForm = document.getElementById('loginForm');
const registerForm = document.getElementById('registerForm');
const message = document.getElementById('authMessage');

async function submitAuthForm(url) {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;

    message.textContent = '';

    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ username, password })
    });

    const data = await response.json();

    if (!response.ok) {
        message.textContent = data.error || 'Authentication failed';
        return;
    }

    window.location.href = '/';
}

if (loginForm) {
    loginForm.addEventListener('submit', function (event) {
        event.preventDefault();
        submitAuthForm('/api/login');
    });
}

if (registerForm) {
    registerForm.addEventListener('submit', function (event) {
        event.preventDefault();
        submitAuthForm('/api/register');
    });
}
