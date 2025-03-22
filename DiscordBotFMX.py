import time
import os
import math
import re
import json
import discord
from discord.ext import commands
from dotenv import load_dotenv
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# Load environment variables
load_dotenv()

USERNAME = os.getenv("AM_USERNAME")
PASSWORD = os.getenv("AM_PASSWORD")
uri = os.getenv("MONGO_AUD")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# MongoDB setup
client = MongoClient(uri)
db = client["FMX"]
collection_hubs = db["Hubs"]

# Initialize Discord bot
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
bot = commands.Bot(command_prefix="!", intents=intents)


# Initialize Selenium WebDriver
def initialize_browser():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--force-renderer-accessibility")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(options=options)
    return driver


def fetch_hubs_from_db():
    hubs = collection_hubs.find()
    # Extract 3-letter IATA codes from each hub name
    return {extract_iata_code(hub["hub_name"]) for hub in hubs if "hub_name" in hub}


# Log in to Airline Manager
def login_to_airline_manager(driver):
    driver.get("https://tycoon.airlines-manager.com/company/profile/network/10615221")
    time.sleep(1)
    try:
        driver.find_element(By.ID, "username").send_keys(USERNAME)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.ID, "loginSubmit").click()
        print("✅ Login successful!")
        time.sleep(2)
    except Exception as e:
        print(f"❌ Error during login: {e}")


# Extract IATA Code from Hub Name
def extract_iata_code(hub_name):
    print(f"Debug: Extracting IATA code from hub_name: {hub_name}")
    match = re.search(r"Hub (\w{3})", hub_name)
    if match:
        return match.group(1)
    else:
        match = re.search(r"(\w{3})", hub_name)
        return match.group(1) if match else None


# Convert Distance to Integer
def clean_distance(distance_str):
    try:
        return int(distance_str.replace(",", "").replace(" km", "").strip())
    except ValueError:
        return None


def close_cookie_banner(driver):
    try:
        cookie_banner = driver.find_element(By.CSS_SELECTOR, ".cc-window.cc-banner")
        if cookie_banner.is_displayed():
            close_button = cookie_banner.find_element(By.CSS_SELECTOR, ".cc-btn.cc-dismiss")
            close_button.click()
            time.sleep(1)
    except Exception as e:
        print(f"lol{e}")


def get_alliance_members(driver):
    try:
        driver.get("https://tycoon.airlines-manager.com/alliance/members")
        member_elements = driver.find_elements(By.XPATH, "//div[@id='underBox']//h3")
        members = []
        for member in member_elements:
            text = member.text.strip()
            if " - " in text:
                username = text.split(" - ")[-1]
                members.append(username)
        return members
    except Exception as e:
        print(f"❌ Error fetching alliance members: {e}")
        return []


# Command to select and visit a member's profile
@bot.command(name='select_member')
async def select_and_visit_user(ctx, driver):

    # Get the list of alliance members
    members = get_alliance_members(driver)

    if not members:
        await ctx.send("❌ No members found in the alliance.")
        driver.quit()
        return

    # Send the list of members to Discord
    await ctx.send("Here are the available alliance members:")
    members_msg = "\n".join([f"{i + 1}. {member}" for i, member in enumerate(members)])  # Create a numbered list
    await ctx.send(f"Available Members:\n{members_msg}")

    # Ask for the user's choice
    await ctx.send("Enter the number of who you are:")

    try:
        close_cookie_banner(driver)
        # Wait for the user's input
        member_choice_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
        choice = int(member_choice_message.content.strip())

        if 1 <= choice <= len(members):
            selected_member = members[choice - 1]

            # Add the logic to visit the selected member's network and fleet
            consult_button = driver.find_element(By.XPATH,
                                                 f"//h3[contains(text(), '{selected_member}')]/../../div[@id='underBox'][2]//a")
            consult_button.click()
            await ctx.send(f"✅{selected_member}!")
            time.sleep(2)
            close_cookie_banner(driver)
            visit_network_and_fleet(driver)
        else:
            await ctx.send("❌ Invalid choice. Please select a valid member number.")

    except Exception as e:
        await ctx.send(f"❌ Error selecting member: {e}")
        driver.quit()


# Function to visit network and fleet
def visit_network_and_fleet(driver):
    try:
        close_cookie_banner(driver)
        network_button = driver.find_element(By.XPATH, "//a[contains(text(), 'Network and fleet')]")
        network_button.click()
        print("✅ Opened Network and Fleet page!")
        time.sleep(2)
    except Exception as e:
        print(f"❌ Error opening Network and Fleet: {e}")
        driver.quit()


def extract_json_map(driver):
    try:
        driver.execute_script("""        
            var element = document.getElementById('map_NetworkJson');
            element.classList.remove('hidden');  
            element.style.display = 'block';    
        """)
        time.sleep(1)
        json_container = driver.find_element(By.ID, 'map_NetworkJson').text
        if not json_container.strip():
            print("❌ No JSON data found inside map_NetworkJson.")
            return set()
        routes = json.loads(json_container)
        excluded_routes = set()
        for route in routes:
            airport2 = route.get("airportTwo", {}).get("iata", "").upper()
            if airport2:
                excluded_routes.add(airport2)
        print(f"🚫 Excluding {len(excluded_routes)} routes from consideration.")
        driver.quit()
        return excluded_routes
    except Exception as e:
        print(f"❌ Error extracting JSON: {e}")
        driver.quit()
        return set()


def get_valid_routes(hub_name, category_aircraft, aircraft_speed, max_range, excluded_routes):
    available_routes = []
    hub_iata = extract_iata_code(hub_name)
    if not hub_iata:
        print("Invalid hub name.")
        return []
    collection = db[hub_iata]
    routes = collection.find()
    for route in routes:
        destination = route.get("destination", "").upper()
        if destination in excluded_routes:
            continue
        distance_clean = clean_distance(route.get("distance", "0 km"))
        if distance_clean is None:
            continue
        category = int(route.get("categories", 0))
        if distance_clean <= max_range and category >= category_aircraft:
            flight_time = (distance_clean / aircraft_speed) * 2
            rounded_flight_time = math.ceil(flight_time * 4) / 4 + 2
            route["estimated_flight_time"] = rounded_flight_time
            route["uses_left"] = 1
            available_routes.append(route)
    print(f"✅ Valid routes after exclusions: {len(available_routes)}")
    return available_routes


def find_valid_circuit(remaining_time, selected_routes, attempted_routes, circuit_number, available_routes):
    available_routes.sort(key=lambda r: r["estimated_flight_time"], reverse=True)
    used_destinations = set(route["destination"] for route in selected_routes)

    for route in available_routes:
        destination = route["destination"]
        flight_time = route["estimated_flight_time"]
        if destination in attempted_routes or destination in used_destinations or route["uses_left"] <= 0:
            continue
        if flight_time > remaining_time:
            continue
        remaining_time -= flight_time
        route["uses_left"] -= 1
        selected_routes.append({"destination": destination, "flight_time": flight_time})
        used_destinations.add(destination)
        if remaining_time == 0:
            print(f"✅ Circuit {circuit_number} completed!")
            return True
        if find_valid_circuit(remaining_time, selected_routes, attempted_routes, circuit_number, available_routes):
            return True

        remaining_time += flight_time
        route["uses_left"] += 1
        selected_routes.pop()
        used_destinations.remove(destination)
        attempted_routes.add(destination)
    return False


def display_circuit(circuit_number, selected_routes):
    total_time = 168
    circuit_msg = f"Circuit {circuit_number} Details:\n"
    destinations = []
    # Collect just the IATA codes of the destinations
    for route in selected_routes:
        destination = route["destination"]
        destinations.append(destination)

    # Join the list of destinations into a compact format
    circuit_msg += ", ".join(destinations)
    circuit_msg += f"\nTotal Flight Time for Circuit {circuit_number}: {total_time} hours\n"

    return circuit_msg


async def create_circuits(available_routes, number_of_circuits, ctx):
    circuit_number = 1
    circuit_details = []

    await ctx.send(f"✅ Starting the creation of {number_of_circuits} circuits...")

    while circuit_number <= number_of_circuits:
        selected_routes = []
        attempted_routes = set()
        remaining_time = 168

        if not find_valid_circuit(remaining_time, selected_routes, attempted_routes, circuit_number, available_routes):
            await ctx.send("✅ All circuits with ≤ 168h have been completed.")
            break
        circuit_details.append(selected_routes)
        circuit_number += 1
    if not circuit_details:
        await ctx.send("❌ No valid circuits were created.")
    return circuit_details


@bot.command(name='cc')
async def create_circuits_command(ctx):
    hubs = fetch_hubs_from_db()
    if not hubs:
        await ctx.send("❌ No hubs found in the database.")
        return

    # Send the list of hubs to Discord
    await ctx.send("Here are the available hubs:")
    hubs_msg = "\n".join(hubs)  # Convert the set into a string
    await ctx.send(f"Available Hubs:\n{hubs_msg}")  # Send the hubs as a message in Discord

    # Ask for the hub name
    await ctx.send("Enter a hub name:")
    hub_name_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
    hub_name = hub_name_message.content.strip()
    await ctx.send("Enter aircraft category:")
    category_aircraft_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
    try:
        category_aircraft = int(category_aircraft_message.content.strip())
    except ValueError:
        await ctx.send("❌ Invalid input. Please enter a valid integer for the aircraft category.")
        return

    await ctx.send("Enter aircraft speed (km/h):")
    aircraft_speed_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
    try:
        aircraft_speed = int(aircraft_speed_message.content.strip())
    except ValueError:
        await ctx.send("❌ Invalid input. Please enter a valid integer for aircraft speed.")
        return

    await ctx.send("Enter max return distance (km):")
    max_range_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
    try:
        max_range = int(max_range_message.content.strip())
    except ValueError:
        await ctx.send("❌ Invalid input. Please enter a valid integer for max return distance.")
        return

    await ctx.send("Enter number of circuits:")
    number_of_circuits_message = await bot.wait_for('message', check=lambda m: m.author == ctx.author)
    try:
        number_of_circuits = int(number_of_circuits_message.content.strip())
    except ValueError:
        await ctx.send("❌ Invalid input. Please enter a valid integer for the number of circuits.")
        return

    driver = initialize_browser()
    login_to_airline_manager(driver)

    # Await the async function here
    await select_and_visit_user(ctx, driver)

    excluded_routes = extract_json_map(driver)
    available_routes = get_valid_routes(hub_name, category_aircraft, aircraft_speed, max_range, excluded_routes)

    if available_routes:
        circuit_details = await create_circuits(available_routes, number_of_circuits, ctx)

        for i, circuit in enumerate(circuit_details, 1):
            circuit_msg = display_circuit(i, circuit)
            await ctx.send(circuit_msg)

    driver.quit()


bot.run(DISCORD_TOKEN)
