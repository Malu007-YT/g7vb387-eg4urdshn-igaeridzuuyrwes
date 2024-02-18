import pytz
import discord
from discord.ext import commands, tasks
import imaplib
import email
import asyncio
import os
import smtplib
import sys
from datetime import datetime
from keep_alive import keep_alive
import subprocess
from email.header import decode_header
from bs4 import BeautifulSoup

keep_alive()

TOKEN = os.environ.get("BOT_TOKEN")

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

intents = discord.Intents.all()
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

auto_clear = True
time_zone = "Europe/Rome"

session_closed_error_count = 0
MAX_SESSION_CLOSED_ERRORS = 5


async def check_activity():
  while True:
    await asyncio.sleep(10)
    if not bot.client.is_connected():
      print("Il bot è offline. Riavvio...")
      await bot.close()


async def stop_bot():
  print("Arresto del bot...")
  await bot.close()


@tasks.loop(seconds=1)
async def check_email_loop():
  files = [
    file_name for file_name in os.listdir()
    if file_name.startswith("credentials_")
  ]
  tasks = []

  for file_name in files:
    user_id = file_name.split("_")[1].split(".")[0]
    task = asyncio.create_task(check_email(user_id))
    tasks.append(task)

  await asyncio.gather(*tasks)


async def handle_socket_error():
  print("Errore di socket. Riavvio...")
  await bot.restart()


async def handle_connection_error():
  print("Errore di connessione. Riavvio...")
  await restart_bot()


async def handle_port_in_use_error():
  print("Porta 8080 in uso da un altro programma. Riavvio...")
  await restart_bot()


async def handle_missing_create_task_error():
  print("Errore interno del bot. Riavvio...")
  await restart_bot()


async def start_check_loops():
  await asyncio.gather(check_email_loop.start(), check_activity.start())


@bot.event
async def on_command_error(ctx, error):
  global session_closed_error_count

  if isinstance(error, commands.CommandInvokeError):
    original = error.original

    if isinstance(original, ConnectionError):
      print("Errore di connessione:", str(original))

      if "Session is closed" in str(original):
        session_closed_error_count += 1

        if session_closed_error_count >= MAX_SESSION_CLOSED_ERRORS:
          print(
            f"L'errore 'Session is closed' si è verificato più di {MAX_SESSION_CLOSED_ERRORS} volte consecutivamente. Non verrà più stampato in console."
          )
        else:
          print(
            f"L'errore 'Session is closed' si è verificato {session_closed_error_count} volte consecutivamente."
          )
      else:
        session_closed_error_count = 0  # Resetta il contatore per altri tipi di errori

      # Verifica se il bot è già offline prima di riavviare
      if not bot.is_closed():
        await restart_bot()
      else:
        print("Il bot è già offline.")

  if isinstance(original, ConnectionError
                ) and "Temporary failure in name resolution" in str(original):
    await handle_connection_error()

  if isinstance(
      original, ConnectionError
  ) and "Si è verificato un errore: Session is closed" in str(original):
    await handle_connection_error()

  if isinstance(
      original,
      OSError) and "8080 è utilizzato da un altro programma" in str(original):
    await handle_port_in_use_error()

  if isinstance(
      original, AttributeError
  ) and "'_MissingSentinel' object has no attribute 'create_task'" in str(
      original):
    await handle_missing_create_task_error()

  if isinstance(original, imaplib.IMAP4.error):
    if "errore socket: EOF" in str(original):
      await handle_socket_error()


def format_email_embed(date, sender, subject, body):
  embed = discord.Embed(title="Nuova Email", color=discord.Color.blue())
  embed.add_field(name="Data",
                  value=date.strftime("%d/%m/%Y %H:%M:%S"),
                  inline=False)
  embed.add_field(name="Mittente", value=sender, inline=False)
  embed.add_field(name="Oggetto", value=subject, inline=False)
  embed.add_field(name="Corpo", value=body, inline=False)
  return embed


async def check_email(user_id):
  await bot.change_presence(activity=discord.Activity(
    type=discord.ActivityType.watching, name="le email"))

  credentials_file = f"credentials_{user_id}.txt"

  if not os.path.isfile(credentials_file):
    print(
      f"File delle credenziali non trovato per l'utente {user_id}. Effettua l'accesso con il comando /login."
    )
    return

  with open(credentials_file, "r") as file:
    credentials = file.read().splitlines()
    email_address = credentials[0]
    password = credentials[1]

  if not email_address.lower().endswith("@gmail.com"):
    print("Attualmente, il servizio è disponibile solo per gli account Gmail!")
    return

  user = await bot.fetch_user(int(user_id))  # Inizializza user prima del ciclo

  while True:
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    try:
      mail.login(email_address, password)
      mail.select("INBOX")

      result, data = mail.search(None, "UNSEEN")
      email_ids = data[0].split()

      for email_id in email_ids:
        is_spam = False  # Inizializza is_spam a False prima del blocco try
        try:
          result, data = mail.fetch(email_id, "(RFC822)")
          raw_email = data[0][1]
          email_message = email.message_from_bytes(raw_email)

          date_tuple = email.utils.parsedate_tz(email_message["Date"])
          if date_tuple:
            local_date = datetime.fromtimestamp(
              email.utils.mktime_tz(date_tuple), pytz.timezone("Europe/Rome"))
          else:
            local_date = datetime.now(pytz.timezone("Europe/Rome"))
          sender = email_message["From"]
          subject = email_message["Subject"]

          email_body = ""
          if email_message.is_multipart():
            for part in email_message.walk():
              content_type = part.get_content_type()
              if content_type == "text/html":
                # Decifra il contenuto HTML utilizzando BeautifulSoup
                email_body = part.get_payload(decode=True).decode(
                  errors='ignore')
                soup = BeautifulSoup(email_body, 'html.parser')
                email_body = soup.get_text(separator=' ', strip=True)
              elif content_type == "text/plain":
                # Decifra il contenuto di testo semplice
                email_body = part.get_payload(decode=True).decode(
                  errors='ignore')
                break
          else:
            email_body = email_message.get_payload(decode=True).decode(
              errors='ignore')

          if len(email_body) > 1024:
            email_body = email_body[:1021] + "..."

          if email_message.get(
              "X-Spam-Flag"
          ) is not None and "spam" in email_message["X-Spam-Flag"].lower():
            is_spam = True

          if user is not None and not is_spam:
            embed = format_email_embed(local_date, sender, subject, email_body)
            dm_channel = user.dm_channel
            if dm_channel is None:
              dm_channel = await user.create_dm()
            message = await dm_channel.send(embed=embed)
            return

          await message.add_reaction("💬")
          await message.add_reaction("⛔")
          await message.add_reaction("🔖")
          await message.add_reaction("⭐")
          await message.add_reaction("🗑️")

          def reaction_check(reaction, user):
            return user.id == user_id and reaction.message.id == message.id

          try:
            while True:
              reaction, _ = await bot.wait_for("reaction_add",
                                               timeout=60.0,
                                               check=reaction_check)
              if str(reaction.emoji) == "💬":
                await dm_channel.send("Inserisci la tua risposta:")
                response_msg = await bot.wait_for(
                  "message",
                  timeout=60.0,
                  check=lambda m: m.author.id == user_id)
                response = response_msg.content

                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp_server:
                  smtp_server.starttls()
                  smtp_server.login(email_address, password)

                  response_subject = f"RE: {subject}"
                  response_body = f"In risposta alla tua email:\n\n{email_body}\n\n---\n{response}"
                  response_message = f"Subject: {response_subject}\n\n{response_body}"

                  smtp_server.sendmail(email_address, sender, response_message)

                await dm_channel.send("Risposta inviata con successo!")
                await dm_channel.send(response)
              elif str(reaction.emoji) == "⛔":
                await message.delete()
              elif str(reaction.emoji) == "🔖":
                mail.store(email_id, "+FLAGS", "\\UnSeen")
              elif str(reaction.emoji) == "⭐":
                mail.store(email_id, "+FLAGS", "\\Flagged")
              elif str(reaction.emoji) == "🗑️":
                mail.store(email_id, "+FLAGS", "\\Deleted")

          except asyncio.TimeoutError:
            pass
          except Exception as e:
            print(f"Si è verificato un errore: {str(e)}")

        except Exception as e:
          print(
            f"Si è verificato un errore durante l'elaborazione dell'email: {str(e)}"
          )

      mail.select("INBOX")
      result, data = mail.search(None, "UNSEEN")
      email_ids = data[0].split()

      for email_id in email_ids:
        try:
          result, data = mail.fetch(email_id, "(RFC822)")
          raw_email = data[0][1]
          email_message = email.message_from_bytes(raw_email)

          date_tuple = email.utils.parsedate_tz(email_message["Date"])
          if date_tuple:
            local_date = datetime.fromtimestamp(
              email.utils.mktime_tz(date_tuple), pytz.timezone("Europe/Rome"))
          else:
            local_date = datetime.now(pytz.timezone("Europe/Rome"))
          sender = email_message["From"]
          subject = email_message["Subject"]

          email_body = ""
          if email_message.is_multipart():
            for part in email_message.walk():
              content_type = part.get_content_type()
              if content_type == "text/plain":
                email_body = part.get_payload(decode=True).decode(
                  errors='ignore')
                break
          else:
            email_body = email_message.get_payload(decode=True).decode(
              errors='ignore')

          if len(email_body) > 1024:
            email_body = email_body[:1021] + "..."

          is_spam = False
          if email_message.get(
              "X-Spam-Flag"
          ) is not None and "spam" in email_message["X-Spam-Flag"].lower():
            is_spam = True

          if user is not None and not is_spam:
            embed = format_email_embed(local_date, sender, subject, email_body)
            dm_channel = user.dm_channel
            if dm_channel is None:
              dm_channel = await user.create_dm()
            message = await dm_channel.send(embed=embed)

        except Exception as e:
          print(
            f"Si è verificato un errore durante l'elaborazione dell'email: {str(e)}"
          )
          print(
            "Arresto del bot a causa di un errore di elaborazione dell'email.")
          return

    except imaplib.IMAP4.error as e:
      print(
        "Errore nell'accesso all'account email. Controlla le tue credenziali.")
      print(str(e))
      os.remove(credentials_file)
      user = await bot.fetch_user(int(user_id))
      if user is not None:
        await user.send(
          "Credenziali email non valide. Controlla le tue credenziali e effettua nuovamente l'accesso."
        )
      return
    except Exception as e:
      print(
        f"Si è verificato un errore nel ciclo di controllo delle email: {str(e)}"
      )
      print("Riavvio del client...")
      await asyncio.sleep(10)
      subprocess.Popen([sys.executable, "main.py"])
      await bot.close()
      return
    finally:
      mail.close()
      mail.logout()

    await asyncio.sleep(1)


@bot.tree.command(name="help", description="lista dei miei comandi!!")
async def ciao(interaction: discord.Interaction):
  await interaction.response.send_message("Commands: !login; !logout;")


@bot.command(name='sync', description='Sincronizza i comandi del bot')
async def sync(ctx):
  print("Comando sync")
  if ctx.author.id == 898475876029706241:
    await bot.tree.sync()
    await ctx.send('Comandi sincronizzati.')
    await restart_bot()
  else:
    await ctx.send('Devi essere il proprietario per usare questo comando!')


@bot.command(name='login', description='Accedi all\'account email')
async def login(ctx):

  def check(message):
    return message.author == ctx.author

  await ctx.send("Inserisci il tuo indirizzo email:")
  email_msg = await bot.wait_for('message', check=check)
  email_address = email_msg.content

  if not email_address.lower().endswith("@gmail.com"):
    await ctx.send(
      "Attualmente, il servizio è disponibile solo per gli account Gmail!")
    return

  await ctx.send("Inserisci la tua password:")
  password_msg = await bot.wait_for('message', check=check)
  password = password_msg.content

  user_id = ctx.author.id
  credentials_file = f"credentials_{user_id}.txt"

  with open(credentials_file, "w") as file:
    file.write(f"{email_address}\n{password}")

  await ctx.send("Credenziali email salvate con successo!")
  await restart_bot()


@bot.command(name="logout", description="Logout dall'applicazione")
async def logout(ctx):
  """Logout dell'utente dall'applicazione."""

  credentials_file = f"credentials_{ctx.author.id}.txt"

  if not os.path.isfile(credentials_file):
    await ctx.send("Non sei loggato.")
    return

  try:
    os.remove(credentials_file)
    await ctx.send("Logout effettuato con successo.")
  except Exception as e:
    await ctx.send(f"Si è verificato un errore durante il logout: {str(e)}")


async def start_bot():
  while True:
    try:
      await bot.start(TOKEN)
    except discord.errors.HTTPException as e:
      if e.status == 429:
        print("Troppe richieste - Riavvio del bot...")
        await restart_bot()
        break
      else:
        print(f"Si è verificato un'eccezione HTTP: {e}")
        break
    except Exception as e:
      print(f"Si è verificato un errore: {e}")
      break


async def on_shutdown():
  print("Arresto del bot...")
  await bot.close()


async def restart_bot():
  print("Riavvio del bot...")
  os.system("pkill -f main.py")
  await asyncio.sleep(1)
  os.system("python3 main.py")


async def main():
  loop = asyncio.get_event_loop()
  loop.create_task(restart_bot())
  while True:
    await asyncio.sleep(10)


@bot.slash_command(name="restart", description="Riavvia il bot solo se eseguito dall'utente specifico")
async def slash_restart(interaction: discord.Interaction):
    # Verifica se l'utente che ha eseguito il comando è quello con l'ID specificato
    if interaction.author.id == 898475876029706241:
        await interaction.response.send_message("Riavvio del bot in corso...")
        await asyncio.sleep(2)
        await bot.close()
        subprocess.Popen([sys.executable, "main.py"])
    else:
        await interaction.response.send_message("Non hai il permesso per eseguire questo comando!")


@bot.event
async def on_ready():
  print("_____________________________________")
  print(f"Il {bot.user} è attivo e pronto!")

  check_email_loop.start(),
  check_activity.start(),

  try:
    synced = await bot.tree.sync()
    print(f"Sincronizzati {len(synced)} comandi")
  except Exception as e:
    print(f"Errore durante la sincronizzazione dei comandi: {e}")

  await bot.change_presence(activity=discord.Activity(
    type=discord.ActivityType.watching, name="le email"))

  files = [
    file_name for file_name in os.listdir()
    if file_name.startswith("credentials_")
  ]
  tasks = []

  for file_name in files:
    user_id = file_name.split("_")[1].split(".")[0]
    task = asyncio.create_task(check_email(user_id))
    tasks.append(task)

  await asyncio.gather(*tasks)

  bot.loop.add_signal_handler('SIGINT',
                              lambda: asyncio.ensure_future(on_shutdown()))


async def restart_bot():
  print("Riavvio del bot in corso...")
  await asyncio.sleep(2)
  subprocess.Popen([sys.executable, "main.py"])
  await bot.close()


@bot.event
async def on_shutdown():
  print("Arresto del bot...")
  await bot.close()


@bot.command(name='restart', description='Riavvia il bot')
async def restart(ctx):
  await ctx.send("Riavvio del bot in corso...")
  await asyncio.sleep(2)
  await bot.restart()


async def start_bot():
  try:
    await bot.start(TOKEN)
  except discord.errors.HTTPException as e:
    if e.status == 429:
      print("Troppe richieste - Riavvio del bot...")
      await restart_bot()
    else:
      print(f"Si è verificato un'eccezione HTTP: {e}")
  except Exception as e:
    print(f"Si è verificato un errore: {e}")
  finally:
    await bot.close()


# Funzione principale che avvia il bot e il ciclo di controllo
async def main():
  while True:
    await asyncio.sleep(1)
    await start_bot()


# Esegui il ciclo principale
asyncio.run(main())
