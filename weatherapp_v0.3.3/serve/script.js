let currentCity = "";
let currentUser = { logged_in: false };
const localHistoryKey = 'weatherLocalHistory';
const localFavoritesKey = 'weatherLocalFavorites';

const ctx = document.getElementById('forecastChart');
let myChart = new Chart(ctx, {
    type: 'line',
    data: {
        labels: [],
        datasets: [{
            label: 'Temperature C',
            data: [],
            borderWidth: 4,
            tension: 0.4,
            borderColor: '#0d6efd',
            backgroundColor: 'rgba(13,110,253,0.1)',
            fill: true
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false
    }
});

async function loadCity(city, saveHistory = true) {
    const saveParam = saveHistory ? '1' : '0';
    const response = await fetch(`/get_data/${encodeURIComponent(city)}?save=${saveParam}`);

    if (!response.ok) {
        alert("Connection Error");
        return;
    }

    const data = await response.json();

    if (data.error) {
        alert(data.error);
        return;
    }

    const weather = data.weather;
    const forecast = data.forecast;
    currentCity = weather.name;

    document.getElementById("cityName").textContent = weather.name;
    document.getElementById("weatherTemp").textContent = Math.round(weather.temp) + " C";
    document.getElementById("humidity").textContent = weather.humidity + "%";
    document.getElementById("wind").textContent = weather.wind + " m/s";
    updateWeatherIcon(weather);
    updateMap(weather.lat, weather.lon, weather.name);

    myChart.data.labels = forecast.map(item => item.day);
    myChart.data.datasets[0].data = forecast.map(item => item.temp);
    myChart.update();

    if (!currentUser.logged_in && saveHistory) {
        addLocalHistory(weather.name);
    }

    await loadHistory();
}

async function loadCurrentUser() {
    const response = await fetch('/current_user');
    if (!response.ok) {
        currentUser = { logged_in: false };
        updateAuthControls();
        return;
    }

    currentUser = await response.json();
    updateAuthControls();
}

function updateAuthControls() {
    const userLabel = document.getElementById('userLabel');
    const loginLink = document.getElementById('loginLink');
    const registerLink = document.getElementById('registerLink');
    const logoutBtn = document.getElementById('logoutBtn');

    if (currentUser.logged_in) {
        userLabel.textContent = currentUser.username;
        userLabel.classList.remove('guest');
        userLabel.classList.add('signed-in');
        loginLink.classList.add('d-none');
        registerLink.classList.add('d-none');
        logoutBtn.classList.remove('d-none');
    } else {
        userLabel.textContent = 'Guest mode';
        userLabel.classList.remove('signed-in');
        userLabel.classList.add('guest');
        loginLink.classList.remove('d-none');
        registerLink.classList.remove('d-none');
        logoutBtn.classList.add('d-none');
    }
}

function updateWeatherIcon(weather) {
    const iconBox = document.getElementById('weatherIcon');
    const conditionText = document.getElementById('weatherCondition');
    const condition = weather.condition || 'Weather';
    const description = weather.description || condition;

    iconBox.innerHTML = '';

    if (weather.icon) {
        const image = document.createElement('img');
        image.src = `https://openweathermap.org/img/wn/${weather.icon}@4x.png`;
        image.alt = description;
        iconBox.appendChild(image);
    } else {
        const fallback = document.createElement('span');
        fallback.className = 'weather-icon-placeholder';
        fallback.textContent = condition.slice(0, 2).toUpperCase();
        iconBox.appendChild(fallback);
    }

    conditionText.textContent = formatWeatherDescription(description);
}

function formatWeatherDescription(description) {
    return description
        .split(' ')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

function updateMap(lat, lon, cityName) {
    const map = document.getElementById('cityMap');
    const mapLink = document.getElementById('openMapLink');
    const offset = 0.08;
    const left = lon - offset;
    const right = lon + offset;
    const bottom = lat - offset;
    const top = lat + offset;
    const bbox = `${left},${bottom},${right},${top}`;

    map.src = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat},${lon}`;
    mapLink.href = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=12/${lat}/${lon}`;
    mapLink.setAttribute('aria-label', `Open larger map for ${cityName}`);
}

async function loadHistory() {
    if (!currentUser.logged_in) {
        renderHistory(getLocalHistory());
        return;
    }

    const response = await fetch('/history');
    if (!response.ok) {
        return;
    }

    const history = await response.json();
    renderHistory(history);
}

function renderHistory(history) {
    const tbody = document.getElementById('historyBody');
    tbody.innerHTML = '';

    if (history.length === 0) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 2;
        cell.className = 'empty-history';
        cell.textContent = 'No search history yet';
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }

    history.forEach(item => {
        const row = document.createElement('tr');
        const cityCell = document.createElement('td');
        const dateCell = document.createElement('td');

        cityCell.textContent = item.city;
        dateCell.textContent = item.searched_at;

        row.appendChild(cityCell);
        row.appendChild(dateCell);
        tbody.appendChild(row);
    });
}

async function loadFavorites() {
    if (!currentUser.logged_in) {
        const container = document.getElementById('favoritesContainer');
        container.innerHTML = '';
        getLocalFavorites().forEach(city => renderFavoriteCard(city));
        return;
    }

    const response = await fetch('/favorites');
    if (!response.ok) {
        return;
    }

    const favorites = await response.json();
    const container = document.getElementById('favoritesContainer');
    container.innerHTML = '';
    favorites.forEach(item => renderFavoriteCard(item.city));
}

function renderFavoriteCard(city) {
    const container = document.getElementById('favoritesContainer');
    const exists = document.querySelector(`[data-city="${city}"]`);

    if (exists || !city) {
        return;
    }

    const column = document.createElement('div');
    const card = document.createElement('div');
    const icon = document.createElement('div');
    const title = document.createElement('h3');
    const button = document.createElement('button');

    column.className = 'col-lg-4';
    card.className = 'favorite-card';
    card.dataset.city = city;
    card.addEventListener('click', () => loadCity(city));

    icon.className = 'city-icon';
    icon.textContent = '*';

    title.textContent = city;

    button.className = 'btn btn-danger btn-sm mt-2';
    button.textContent = 'Remove';
    button.addEventListener('click', event => removeFavorite(event, city));

    card.appendChild(icon);
    card.appendChild(title);
    card.appendChild(button);
    column.appendChild(card);
    container.appendChild(column);
}

async function removeFavorite(event, city) {
    event.stopPropagation();

    if (!currentUser.logged_in) {
        const favorites = getLocalFavorites().filter(item => item !== city);
        setLocalFavorites(favorites);
        await loadFavorites();
        return;
    }

    const response = await fetch(`/favorites/${encodeURIComponent(city)}`, {
        method: 'DELETE'
    });

    if (!response.ok) {
        alert("Could not remove favorite");
        return;
    }

    await loadFavorites();
}

document.getElementById('searchBtn').addEventListener('click', function () {
    const val = document.getElementById('searchInput').value.trim();
    if (val === '') {
        alert('Please enter a city name!');
        return;
    }

    loadCity(val);
    document.getElementById('searchInput').value = '';
});

document.getElementById('searchInput').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
        document.getElementById('searchBtn').click();
    }
});

document.getElementById('addFavoriteBtn').addEventListener('click', async function () {
    if (!currentCity) {
        alert("Load a city first");
        return;
    }

    if (!currentUser.logged_in) {
        const favorites = getLocalFavorites();
        if (favorites.includes(currentCity)) {
            alert("This city is already in favorites!");
            return;
        }

        favorites.push(currentCity);
        setLocalFavorites(favorites);
        await loadFavorites();
        return;
    }

    const response = await fetch('/favorites', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ city: currentCity })
    });

    if (!response.ok) {
        const data = await response.json();
        alert(data.error || "Could not add favorite");
        return;
    }

    await loadFavorites();
});

document.getElementById('clearHistoryBtn').addEventListener('click', async function () {
    if (!confirm('Clear your search history?')) {
        return;
    }

    if (!currentUser.logged_in) {
        localStorage.removeItem(localHistoryKey);
        renderHistory([]);
        return;
    }

    const response = await fetch('/history', {
        method: 'DELETE'
    });

    if (!response.ok) {
        alert('Could not clear search history');
        return;
    }

    await loadHistory();
});

function getLocalHistory() {
    return JSON.parse(localStorage.getItem(localHistoryKey) || '[]');
}

function addLocalHistory(city) {
    const searchedAt = new Date().toLocaleString();
    const history = getLocalHistory();
    history.unshift({
        city: city,
        searched_at: searchedAt
    });
    localStorage.setItem(localHistoryKey, JSON.stringify(history.slice(0, 20)));
}

function getLocalFavorites() {
    return JSON.parse(localStorage.getItem(localFavoritesKey) || '[]');
}

function setLocalFavorites(favorites) {
    localStorage.setItem(localFavoritesKey, JSON.stringify(favorites));
}

document.getElementById('themeToggle').addEventListener('click', function () {
    document.body.classList.toggle('dark-mode');

    if (document.body.classList.contains('dark-mode')) {
        this.textContent = 'Light Mode';
    } else {
        this.textContent = 'Dark Mode';
    }
});

document.getElementById('logoutBtn').addEventListener('click', async function () {
    await fetch('/api/logout', {
        method: 'POST'
    });
    window.location.href = '/';
});

async function initializeDashboard() {
    await loadCurrentUser();
    await loadFavorites();
    await loadHistory();
    await loadCity("wroclaw", false);
}

initializeDashboard();
