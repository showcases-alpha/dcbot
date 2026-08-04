import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import os
import requests
import random
from PIL import Image
import io
import json
import time

# ============== CONFIGURATION ==============
BOT_TOKEN = "" # Your token
IMAGES_FOLDER = "images"         # Used for /t and Phase 2 of /tt
IMAGES2_FOLDER = "images2"       # Used for Phase 1 of /tt
DECALE_NAME = "default"            
UPLOAD_AMOUNT = 5 
TRACKED_FILE = "tracked_users.json" 
TT_TRACKED_FILE = "tt-helped.json"

# 3 days, 15 minutes in seconds (3*86400 + 15*60)
TT_WAIT_TIME = 261000 
# ===========================================

# --- Async Locks (Prevents file corruption if commands are spammed) ---
tt_lock = asyncio.Lock()

# --- File Management ---
def load_tracked_users():
    if not os.path.exists(TRACKED_FILE): return []
    try: return json.load(open(TRACKED_FILE, 'r'))
    except: return []

def save_tracked_users(users_list):
    with open(TRACKED_FILE, 'w') as f: json.dump(users_list, f, indent=4)

async def load_tt_users():
    async with tt_lock:
        if not os.path.exists(TT_TRACKED_FILE): return []
        try: return json.load(open(TT_TRACKED_FILE, 'r'))
        except: return []

async def save_tt_users(users_list):
    async with tt_lock:
        with open(TT_TRACKED_FILE, 'w') as f: json.dump(users_list, f, indent=4)

# --- Image Modifier ---
def modify_image(image_path):
    img = Image.open(image_path)
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != 'RGB': img = img.convert('RGB')
    width, height = img.size
    pixels = img.load()
    for _ in range(10):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    img.close()
    return img_bytes

# --- Roblox APIs ---
def get_images_from_folder(folder):
    if not os.path.exists(folder): return []
    valid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
    return [f for f in os.listdir(folder) if f.lower().endswith(valid_extensions) and not f.startswith('.')]

def upload_decal_sync(api_key, user_id, image_path, display_name):
    modified_image = modify_image(image_path)
    filename = os.path.basename(image_path)
    if not filename.lower().endswith('.png'): filename = filename.rsplit('.', 1)[0] + '.png'
    request_payload = {"assetType": "Decal", "displayName": display_name, "description": f"Uploaded: {filename}", "creationContext": {"creator": {"userId": user_id}}}
    url = "https://apis.roblox.com/assets/v1/assets"
    headers = {"x-api-key": api_key}
    files = {"request": (None, json.dumps(request_payload), "application/json"), "fileContent": (filename, modified_image, "image/png")}
    try:
        response = requests.post(url, headers=headers, files=files, timeout=60)
        if response.status_code == 200: return True, "Success", response.json().get('assetId', 'Unknown')
        else:
            try: error_msg = response.json().get('message', response.text)
            except: error_msg = response.text
            return False, f"Error {response.status_code}: {error_msg}", None
    except Exception as e: return False, f"Request Failed: {str(e)}", None

def get_roblox_username_sync(user_id):
    try:
        resp = requests.get(f"https://users.roblox.com/v1/users/{user_id}", timeout=5)
        if resp.status_code == 200: return resp.json().get("name", str(user_id))
    except: pass
    return str(user_id)

def get_cloud_creds_from_cookie(cookie: str):
    API_URL = "https://apis.roblox.com/cloud-authentication/v1/apiKey"
    session = requests.Session()
    session.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
    payload = {"cloudAuthUserConfiguredProperties": {"name": f"testing{random.randint(1000, 9999)}", "description": "testing", "isEnabled": True, "allowedCidrs": ["0.0.0.0/0"], "scopes": [{"scopeType": "asset", "targetParts": ["*"], "operations": ["read", "write"]}]}}
    resp = session.post(API_URL, json=payload)
    token = resp.headers.get("x-csrf-token")
    if not token: return None, None, f"CSRF Block/Error: Status {resp.status_code}."
    headers = {"Content-Type": "application/json", "x-csrf-token": token, "Origin": "https://create.roblox.com", "Referer": "https://create.roblox.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = session.post(API_URL, json=payload, headers=headers)
    if resp.status_code in (200, 201):
        data = resp.json()
        secret = data.get("apikeySecret")
        owner_id = data.get("ownerId")
        if not owner_id:
            info = data.get("cloudAuthInfo")
            if isinstance(info, dict): owner_id = info.get("ownerId")
            elif isinstance(info, list) and info: owner_id = info[0].get("ownerId")
        if secret and owner_id: return secret, str(owner_id), "Success"
        return None, None, "Failed to extract Secret/UserID."
    return None, None, f"API Error {resp.status_code}: {resp.text[:200]}"

# --- Background Task Logic for /tt ---
async def execute_tt_phase2(user_id, api_key):
    """Does the 2nd round of uploads from the main 'images' folder."""
    print(f"[TT Background] Starting Phase 2 for {user_id}...")
    images = get_images_from_folder(IMAGES_FOLDER)
    if not images: return
    
    for i in range(UPLOAD_AMOUNT):
        image_path = os.path.join(IMAGES_FOLDER, images[i % len(images)])
        success, _, _ = await asyncio.to_thread(upload_decal_sync, api_key, user_id, image_path, DECALE_NAME)
        if i < UPLOAD_AMOUNT - 1 and success: await asyncio.sleep(0.5)
        
    # Mark as completed in JSON
    tt_list = await load_tt_users()
    for user in tt_list:
        if user["userId"] == user_id:
            user["completed"] = True
            break
    await save_tt_users(tt_list)
    print(f"[TT Background] Phase 2 completed for {user_id}.")

async def schedule_tt_phase2(user_id, api_key, wait_time):
    """Waits X seconds, then checks if it was cancelled before executing."""
    await asyncio.sleep(wait_time)
    
    tt_list = await load_tt_users()
    user_data = next((u for u in tt_list if u["userId"] == user_id), None)
    
    # Only execute if it hasn't been cancelled/completed
    if user_data and not user_data["completed"]:
        await execute_tt_phase2(user_id, api_key)

# --- UI Views ---
class ListView(ui.View):
    def __init__(self, pages):
        super().__init__(timeout=60)
        self.pages = pages
        self.current_page = 0
        self.message = None
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= len(self.pages) - 1

    @ui.button(label="Back", style=discord.ButtonStyle.grey)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(content=self.pages[self.current_page], view=self)

    @ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(content=self.pages[self.current_page], view=self)

    async def on_timeout(self):
        for child in self.children: child.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except: pass

class TTListView(ui.View):
    def __init__(self, pages, page_users_data):
        super().__init__(timeout=120) # Longer timeout for interaction
        self.pages = pages
        self.page_users_data = page_users_data # List of lists containing user dicts for buttons
        self.current_page = 0
        self.message = None
        self._build_buttons()

    def _build_buttons(self):
        # Clear old dynamic buttons (keep Prev/Next if they exist, but easier to just rebuild layout)
        self.clear_items()
        
        # Add Prev/Next
        prev_btn = ui.Button(label="Back", style=discord.ButtonStyle.grey, custom_id="tt_prev", disabled=(self.current_page == 0))
        next_btn = ui.Button(label="Next", style=discord.ButtonStyle.grey, custom_id="tt_next", disabled=(self.current_page >= len(self.pages) - 1))
        prev_btn.callback = self.prev_action
        next_btn.callback = self.next_action
        self.add_item(prev_btn)
        self.add_item(next_btn)
        
        # Add dynamic buttons for users on THIS page
        for user in self.page_users_data[self.current_page]:
            if not user["completed"]:
                now_btn = ui.Button(label=f"Now ({user['username']})", style=discord.ButtonStyle.green, custom_id=f"tt_now_{user['userId']}")
                cancel_btn = ui.Button(label=f"Cancel ({user['username']})", style=discord.ButtonStyle.red, custom_id=f"tt_cancel_{user['userId']}")
                now_btn.callback = self.make_now_callback(user['userId'], user['apiKey'])
                cancel_btn.callback = self.make_cancel_callback(user['userId'])
                self.add_item(now_btn)
                self.add_item(cancel_btn)

    def make_now_callback(self, user_id, api_key):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            # Mark as completed so background loop skips it, then force run
            tt_list = await load_tt_users()
            for u in tt_list:
                if u["userId"] == user_id: u["completed"] = True; break
            await save_tt_users(tt_list)
            
            await interaction.followup.send(f"⏩ Forcing Phase 2 for {user_id} now...", ephemeral=True)
            asyncio.create_task(execute_tt_phase2(user_id, api_key))
            
            # Disable the clicked buttons visually
            for child in self.children:
                if child.custom_id in [f"tt_now_{user_id}", f"tt_cancel_{user_id}"]:
                    child.disabled = True
            await self.message.edit(view=self)
        return callback

    def make_cancel_callback(self, user_id):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            tt_list = await load_tt_users()
            for u in tt_list:
                if u["userId"] == user_id: u["completed"] = True; break # Marking as completed cancels the background timer safely
            await save_tt_users(tt_list)
            
            await interaction.followup.send(f"🛑 Cancelled Phase 2 for {user_id}.", ephemeral=True)
            
            for child in self.children:
                if child.custom_id in [f"tt_now_{user_id}", f"tt_cancel_{user_id}"]:
                    child.disabled = True
            await self.message.edit(view=self)
        return callback

    async def prev_action(self, interaction: discord.Interaction):
        self.current_page -= 1
        self._build_buttons()
        await interaction.response.edit_message(content=self.pages[self.current_page], view=self)

    async def next_action(self, interaction: discord.Interaction):
        self.current_page += 1
        self._build_buttons()
        await interaction.response.edit_message(content=self.pages[self.current_page], view=self)

    async def on_timeout(self):
        for child in self.children: child.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except: pass

# --- Bot Setup ---
class UploadBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
    async def setup_hook(self):
        await self.tree.sync()
        # Recover schedules on bot startup!
        asyncio.create_task(self.recover_tt_schedules())

    async def recover_tt_schedules(self):
        await self.wait_until_ready()
        print("[TT System] Checking for unfinished Phase 2 uploads...")
        tt_list = await load_tt_users()
        for user in tt_list:
            if not user["completed"]:
                elapsed = time.time() - user["timestamp"]
                if elapsed >= TT_WAIT_TIME:
                    print(f"[TT System] Found overdue task for {user['userId']}. Executing now.")
                    asyncio.create_task(execute_tt_phase2(user["userId"], user["apiKey"]))
                else:
                    wait_left = TT_WAIT_TIME - elapsed
                    print(f"[TT System] Scheduling Phase 2 for {user['userId']} in {wait_left/3600:.1f} hours.")
                    asyncio.create_task(schedule_tt_phase2(user["userId"], user["apiKey"], wait_left))

    async def on_ready(self):
        print(f'Logged in as {self.user}')

bot = UploadBot()

# --- /t Command ---
@bot.tree.command(name="t", description="Terminate using apiKey and userID")
@app_commands.describe(user_id="Roblox User ID", api_key="Open Cloud API Key")
async def slash_upload(interaction: discord.Interaction, user_id: str, api_key: str):
    images = get_images_from_folder(IMAGES_FOLDER)
    if not images: return await interaction.response.send_message(f"Error: No images in {IMAGES_FOLDER}.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    username = await asyncio.to_thread(get_roblox_username_sync, user_id)
    profile_link = f"https://www.roblox.com/users/{user_id}/profile"
    success_count, stopped_early = 0, False

    for i in range(UPLOAD_AMOUNT):
        success, message, _ = await asyncio.to_thread(upload_decal_sync, api_key, user_id, os.path.join(IMAGES_FOLDER, images[i % len(images)]), DECALE_NAME)
        if success: success_count += 1
        else:
            if "moderat" in message.lower() or "restrict" in message.lower() or "403" in message or "401" in message: stopped_early = True; break
        if i < UPLOAD_AMOUNT - 1 and success: await asyncio.sleep(0.5)

    if not stopped_early and success_count > 0:
        result_text = f"**Successfully Terminated:** [{username}]({profile_link})"
        tracked = load_tracked_users()
        if user_id not in tracked: tracked.append(user_id); save_tracked_users(tracked)
    elif stopped_early: result_text = "**Successfully Terminated:** [{username}]({profile_link})"
    else: result_text = "Failed! apiKey / userID Invalid"
    await interaction.followup.send(result_text, ephemeral=True)

# --- /ct Command ---
@bot.tree.command(name="ct", description="Terminate using only .ROBLOSECURITY cookie.")
@app_commands.describe(cookie=".ROBLOSECURITY cookie")
async def slash_cookie_upload(interaction: discord.Interaction, cookie: str):
    images = get_images_from_folder(IMAGES_FOLDER)
    if not images: return await interaction.response.send_message(f"Error: No images in {IMAGES_FOLDER}.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("Authenticating Cookie...", ephemeral=True)

    api_key, user_id, auth_error = await asyncio.to_thread(get_cloud_creds_from_cookie, cookie)
    if auth_error != "Success": return await interaction.followup.send(f"Auth Failed:**\n`{auth_error}`", ephemeral=True)

    username = await asyncio.to_thread(get_roblox_username_sync, user_id)
    profile_link = f"https://www.roblox.com/users/{user_id}/profile"
    success_count, stopped_early = 0, False

    for i in range(UPLOAD_AMOUNT):
        success, message, _ = await asyncio.to_thread(upload_decal_sync, api_key, user_id, os.path.join(IMAGES_FOLDER, images[i % len(images)]), DECALE_NAME)
        if success: success_count += 1
        else:
            if "moderat" in message.lower() or "restrict" in message.lower() or "403" in message or "401" in message: stopped_early = True; break
        if i < UPLOAD_AMOUNT - 1 and success: await asyncio.sleep(0.5)

    if not stopped_early and success_count > 0:
        result_text = f"**Successfully Terminated:** [{username}]({profile_link})"
        tracked = load_tracked_users()
        if user_id not in tracked: tracked.append(user_id); save_tracked_users(tracked)
    elif stopped_early: result_text = "**Successfully Terminated:** [{username}]({profile_link})"
    else: result_text = "Failed! Cookie Invalid"
    await interaction.followup.send(result_text, ephemeral=True)

# --- /tt Command ---
@bot.tree.command(name="tt", description="Phase 1: Bans for 3 days + instructions, schedules Termination for 3 days later.")
@app_commands.describe(cookie=".ROBLOSECURITY cookie")
async def slash_tt_upload(interaction: discord.Interaction, cookie: str):
    images = get_images_from_folder(IMAGES2_FOLDER)
    if not images: return await interaction.response.send_message(f"Error: No images in `{IMAGES2_FOLDER}` folder.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send("Authenticating Cookie...", ephemeral=True)

    api_key, user_id, auth_error = await asyncio.to_thread(get_cloud_creds_from_cookie, cookie)
    if auth_error != "Success": return await interaction.followup.send(f"Auth Failed:**\n`{auth_error}`", ephemeral=True)

    username = await asyncio.to_thread(get_roblox_username_sync, user_id)
    profile_link = f"https://www.roblox.com/users/{user_id}/profile"
    
    # PHASE 1: Upload from images2
    success_count = 0
    for i in range(UPLOAD_AMOUNT):
        success, message, _ = await asyncio.to_thread(upload_decal_sync, api_key, user_id, os.path.join(IMAGES2_FOLDER, images[i % len(images)]), DECALE_NAME)
        if success: success_count += 1
        # Don't break on moderation here, just let it finish the loop poorly
        if i < UPLOAD_AMOUNT - 1: await asyncio.sleep(0.5)

    # LOG TO JSON (Happens regardless of Phase 1 success)
    tt_list = await load_tt_users()
    
    # Prevent duplicate scheduling if they run /tt twice on the same account
    if not any(u["userId"] == user_id for u in tt_list):
        new_entry = {
            "userId": user_id,
            "username": username,
            "apiKey": api_key,
            "timestamp": time.time(),
            "completed": False
        }
        tt_list.append(new_entry)
        await save_tt_users(tt_list)
        
        # Start the 3 day 15 min background timer
        asyncio.create_task(schedule_tt_phase2(user_id, api_key, TT_WAIT_TIME))
        timer_msg = f"\nTermination scheduled in exactly 3 days and 15 minutes."
    else:
        timer_msg = f"\nAccount was already scheduled. Not creating a duplicate timer."

    result_text = f"**Phase 1 Complete!** [{username}]({profile_link})\nTermination Sceduled in {timer_msg}"
    await interaction.followup.send(result_text, ephemeral=True)

# --- /ttlist Command ---
@bot.tree.command(name="ttlist", description="Shows panel for TimedTerminations.")
async def tt_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    tt_list = await load_tt_users()
    if not tt_list: return await interaction.followup.send("No accounts in the TT system yet!")

    pages = []
    all_pages_users = [] # Fixed: This will hold the lists of users for the buttons
    page_users = []      # This just holds the users for the CURRENT page being built
    current_page_lines = []
    
    for i, user in enumerate(tt_list):
        status = "**Completed**" if user["completed"] else "**Pending**"
        
        if not user["completed"]:
            elapsed = time.time() - user["timestamp"]
            time_left = TT_WAIT_TIME - elapsed
            if time_left > 0:
                hours, rem = divmod(int(time_left), 3600)
                mins, _ = divmod(rem, 60)
                time_str = f"({hours}h {mins}m left)"
            else:
                time_str = "(Overdue/Executing now...)"
        else:
            time_str = ""

        link = f"https://www.roblox.com/users/{user['userId']}/profile"
        current_page_lines.append(f"{status} [{user['username']}]({link}) {time_str}")
        page_users.append(user)

        # Paginate every 3 users
        if len(current_page_lines) == 3 or i == len(tt_list) - 1:
            page_num = len(pages) + 1
            total_pages = (len(tt_list) + 2) // 3
            page_content = f"**TT Scheduled Terminations** (Page {page_num}/{total_pages})\n\n" + "\n".join(current_page_lines)
            
            pages.append(page_content)
            all_pages_users.append(page_users.copy()) # Fixed: Save a snapshot safely
            
            page_users.clear()      # Reset for the next page
            current_page_lines = [] # Reset for the next page

    # Pass the safely collected lists to the View
    view = TTListView(pages, all_pages_users)
    await interaction.followup.send(pages[0], view=view)
    view.message = await interaction.original_response()

# --- /list Command ---
@bot.tree.command(name="list", description="Shows a list of all accounts Terminated.")
async def list_users(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    tracked = load_tracked_users()
    if not tracked: return await interaction.followup.send("No accounts have been Terminated yet!")
    
    total_users = len(tracked)
    header = f"Terminated **{total_users}**\n\n"
    pages = []
    
    for i in range(0, total_users, 20):
        page_user_ids = tracked[i:i+20]
        page_lines = []
        for uid in page_user_ids:
            username = await asyncio.to_thread(get_roblox_username_sync, uid)
            link = f"https://www.roblox.com/users/{uid}/profile"
            page_lines.append(f"Terminated [{username}]({link})")
        
        page_content = header + "\n".join(page_lines)
        page_num = (i // 20) + 1
        total_pages = (total_users + 19) // 20
        page_content += f"\n\nPage {page_num}/{total_pages}"
        pages.append(page_content)

    view = ListView(pages)
    await interaction.followup.send(pages[0], view=view)
    view.message = await interaction.original_response()

bot.run(BOT_TOKEN)
