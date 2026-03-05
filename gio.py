import pygame
import sys
import time

import random

# ──────────────── Settings ────────────────
SCREEN_WIDTH = 1000
SCREEN_HEIGHT = 700
CANVAS_X, CANVAS_Y = 200, 60
CANVAS_W, CANVAS_H = 780, 520
FPS = 60
ROUND_TIME = 60  # seconds per round
TOTAL_ROUNDS = 6


# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (200, 200, 200)
DARK_GRAY = (80, 80, 80)
LIGHT_GRAY = (230, 230, 230)
BG_COLOR = (240, 240, 245)
PANEL_COLOR = (50, 55, 70)
ACCENT = (80, 140, 255)
CORRECT_GREEN = (40, 200, 80)
WRONG_RED = (220, 60, 60)

# Drawing palette
PALETTE = [
    (0, 0, 0),        # Black
    (255, 255, 255),   # White
    (200, 200, 200),   # Light gray
    (255, 0, 0),       # Red
    (255, 100, 0),     # Orange
    (255, 220, 0),     # Yellow
    (0, 180, 0),       # Green
    (0, 200, 200),     # Cyan
    (0, 80, 255),      # Blue
    (140, 0, 255),     # Purple
    (255, 0, 200),     # Pink
    (140, 80, 20),     # Brown
]

BRUSH_SIZES = [3, 6, 10, 18, 30]

# Word list
WORDS = [
    "cat", "dog", "house", "tree", "sun", "moon", "car", "fish",
    "bird", "flower", "star", "heart", "boat", "plane", "apple",
    "banana", "pizza", "guitar", "camera", "clock", "umbrella",
    "rainbow", "mountain", "beach", "robot", "rocket", "dragon",
    "castle", "butterfly", "snowman", "bicycle", "bridge", "diamond",
    "elephant", "fire", "ghost", "hat", "ice cream", "jungle",
    "key", "lamp", "mushroom", "ocean", "penguin", "queen",
    "snake", "tornado", "volcano", "waterfall", "zebra", "sword",
    "crown", "skull", "cactus", "donut", "egg", "frog", "grapes",
    "helicopter", "island", "jellyfish", "kite", "lion", "mermaid",
    "ninja", "octopus", "pirate", "rose", "spider", "treasure",
    "unicorn", "wizard", "angel", "bomb", "candle", "dice",
]


def main():
    pygame.init()
    # Fullscreen toggle support
    global SCREEN_WIDTH, SCREEN_HEIGHT, CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H
    fullscreen = True
    def set_screen(full):
        global screen, SCREEN_WIDTH, SCREEN_HEIGHT, CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H
        if full:
            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            screen = pygame.display.set_mode((1200, 800))
        SCREEN_WIDTH, SCREEN_HEIGHT = screen.get_size()
        CANVAS_X, CANVAS_Y = 200, 60
        CANVAS_W, CANVAS_H = SCREEN_WIDTH - 220, SCREEN_HEIGHT - 180
    set_screen(fullscreen)
    pygame.display.set_caption("Skribbl — Drawing & Guessing Game")
    clock = pygame.time.Clock()

    # Fonts
    font_big = pygame.font.SysFont("segoeui", 36, bold=True)
    font_med = pygame.font.SysFont("segoeui", 22)
    font_small = pygame.font.SysFont("segoeui", 18)
    font_tiny = pygame.font.SysFont("segoeui", 14)
    font_input = pygame.font.SysFont("consolas", 20)

    # ──────────── Game state ────────────
    class Game:
        def __init__(self):
            self.phase = "menu"  # menu | show_word | drawing | result | game_over
            self.current_round = 0
            self.score = 0
            self.word = ""
            self.hint = ""
            self.guess_text = ""
            self.guess_result = ""  # "" | "correct" | "wrong"
            self.result_timer = 0
            self.round_start = 0
            self.time_left = ROUND_TIME
            # Canvas
            self.canvas = pygame.Surface((CANVAS_W, CANVAS_H))
            self.canvas.fill(WHITE)
            self.draw_color = BLACK
            self.brush_size = 6
            self.drawing = False
            self.last_pos = None
            # History for guesses
            self.guesses = []
            self.hints_given = 0
            self.show_word_time = 0
            self.drawer = "player"  # 'player' or 'ai'

        def new_round(self):
            self.current_round += 1
            if self.current_round > TOTAL_ROUNDS:
                self.phase = "game_over"
                return
            # 1-3: player draws, 4-6: AI draws
            if self.current_round <= 3:
                self.drawer = "player"
            else:
                self.drawer = "ai"
            self.word = random.choice(WORDS)
            self.hint = "_ " * len(self.word.replace(" ", ""))
            self.guess_text = ""
            self.guess_result = ""
            self.guesses = []
            self.hints_given = 0
            self.canvas.fill(WHITE)
            self.draw_color = BLACK
            self.brush_size = 6
            self.drawing = False
            self.last_pos = None
            self.phase = "drawing"
            self.round_start = time.time()
            self.time_left = ROUND_TIME
            self.show_word_time = time.time()
            self.drawing_done = False
            self.guessing_started = False
            self.ai_draw_steps = None
            self.ai_draw_step_idx = 0
            # Reset AI draw state for new round
            if hasattr(self, 'ai_drawn'):
                self.ai_drawn = False
            if hasattr(self, 'ai_drawn_time'):
                self.ai_drawn_time = None

        def start_guessing(self):
            self.phase = "guessing"
            self.guess_text = ""
            self.guess_result = ""
            self.guesses = []
            self.hints_given = 0
            self.round_start = time.time()
            self.time_left = ROUND_TIME

        def build_hint(self):
            """Reveal some letters as hints."""
            word_chars = list(self.word)
            hidden = list(self.hint.replace(" ", ""))
            # Pick an unrevealed position
            unrevealed = [i for i, c in enumerate(word_chars) if c != " " and hidden[i] == "_"]
            if unrevealed:
                idx = random.choice(unrevealed)
                hidden[idx] = word_chars[idx]
                self.hints_given += 1
            self.hint = " ".join(hidden)

    g = Game()

    # ──────────────── AI Player State ────────────────
    class AIPlayer:
        def __init__(self):
            self.last_guess_time = 0
            self.guess_interval = 5.0  # seconds between guesses
            self.active = True  # Set to True to enable AI guessing
            self.last_hint = ""
            self.tried_words = set()

        def make_guess(self, hint):
            # Try to guess the word from the hint
            # If hint is all underscores, guess random word
            clean_hint = hint.replace(" ", "")
            possible = []
            for w in WORDS:
                if len(w.replace(" ", "")) != len(clean_hint):
                    continue
                match = True
                for i, c in enumerate(clean_hint):
                    if c != "_" and w.replace(" ", "")[i] != c:
                        match = False
                        break
                if match and w not in self.tried_words:
                    possible.append(w)
            if possible:
                guess = random.choice(possible)
            else:
                # fallback: random word not tried
                guess = random.choice([w for w in WORDS if w not in self.tried_words])
            self.tried_words.add(guess)
            return guess

        def reset(self):
            self.last_guess_time = 0
            self.tried_words = set()

    ai = AIPlayer()

    def draw_rounded_rect(surface, color, rect, radius=8):
        pygame.draw.rect(surface, color, rect, border_radius=radius)

    def draw_button(surface, text, rect, color=ACCENT, text_color=WHITE, f=font_med):
        draw_rounded_rect(surface, color, rect, 10)
        txt = f.render(text, True, text_color)
        surface.blit(txt, txt.get_rect(center=rect.center))

    def point_in_rect(pos, rect):
        return rect.collidepoint(pos)

    # ──────────────── Main loop ────────────────
    running = True
    # --- Page scroll state ---
    page_scroll_offset = 0
    PAGE_SCROLLBAR_W = 18
    # Set a virtual page height (can be larger than screen)
    VIRTUAL_PAGE_HEIGHT = max(1200, SCREEN_HEIGHT)

    while running:
        dt = clock.tick(FPS)
        mx, my = pygame.mouse.get_pos()

        events = pygame.event.get()
        for event in events:
            # Only skip mouse events for drawing and guess history during AI guessing phase
            if g.phase == "guessing" and g.drawer == "ai":
                if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
                    # Still allow fullscreen and page scrollbar mouse events
                    # Only skip mouse events that would affect drawing or guess history
                    # (Drawing and guess history mouse logic is already guarded by flags)
                    pass  # Do not continue; allow event loop to process keyboard and essential UI

            # Always allow quit and fullscreen events
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F11:
                    fullscreen = not fullscreen
                    set_screen(fullscreen)
                elif event.key == pygame.K_ESCAPE and fullscreen:
                    fullscreen = False
                    set_screen(fullscreen)

            # Only process game logic if not in game_over phase
            if g.phase == "game_over":
                if event.type == pygame.MOUSEBUTTONDOWN:
                    play_btn = pygame.Rect(SCREEN_WIDTH // 2 - 120, 480, 240, 55)
                    if point_in_rect(event.pos, play_btn):
                        g.__init__()
                        g.new_round()
                continue

            # ── MENU ──
            if g.phase == "menu":
                if event.type == pygame.MOUSEBUTTONDOWN:
                    play_btn = pygame.Rect(SCREEN_WIDTH // 2 - 120, 400, 240, 55)
                    if point_in_rect(event.pos, play_btn):
                        g.__init__()
                        ai.reset()
                        g.new_round()
                continue

            # (No more show_word phase)

            # ── DRAWING PHASE ──
            if g.phase == "drawing":
                # Drawing phase: only drawer can draw, others wait
                if g.drawer == "ai":
                    # Gradual AI drawing: draw a little each frame
                    if g.ai_draw_steps is None:
                        g.canvas.fill(WHITE)
                        g.ai_draw_steps = []
                        for _ in range(12):
                            color = random.choice(PALETTE)
                            x1 = random.randint(0, CANVAS_W)
                            y1 = random.randint(0, CANVAS_H)
                            x2 = random.randint(0, CANVAS_W)
                            y2 = random.randint(0, CANVAS_H)
                            size = random.choice(BRUSH_SIZES)
                            g.ai_draw_steps.append(('line', color, x1, y1, x2, y2, size))
                        for _ in range(4):
                            color = random.choice(PALETTE)
                            x = random.randint(0, CANVAS_W-40)
                            y = random.randint(0, CANVAS_H-40)
                            w = random.randint(20, 80)
                            h = random.randint(20, 80)
                            g.ai_draw_steps.append(('ellipse', color, x, y, w, h))
                        g.ai_draw_step_idx = 0
                    if g.ai_draw_step_idx < len(g.ai_draw_steps):
                        step = g.ai_draw_steps[g.ai_draw_step_idx]
                        if step[0] == 'line':
                            _, color, x1, y1, x2, y2, size = step
                            pygame.draw.line(g.canvas, color, (x1, y1), (x2, y2), size)
                        elif step[0] == 'ellipse':
                            _, color, x, y, w, h = step
                            pygame.draw.ellipse(g.canvas, color, (x, y, w, h), 0)
                        g.ai_draw_step_idx += 1
                    else:
                        g.drawing_done = True
                else:
                    # Player drawing (only if drawing_mouse_ui_enabled)
                    if drawing_mouse_ui_enabled:
                        if event.type == pygame.MOUSEBUTTONDOWN:
                            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                                # Check palette clicks
                                palette_clicked = False
                                for i, color in enumerate(PALETTE):
                                    cx = 20 + (i % 2) * 40
                                    cy = 80 + (i // 2) * 40
                                    crect = pygame.Rect(cx, cy, 34, 34)
                                    if point_in_rect(event.pos, crect):
                                        g.draw_color = color
                                        palette_clicked = True
                                        break
                                # Check brush size clicks
                                if not palette_clicked:
                                    for i, size in enumerate(BRUSH_SIZES):
                                        bx = 20 + i * 34
                                        by = CANVAS_Y + CANVAS_H + 20
                                        brect = pygame.Rect(bx, by, 30, 30)
                                        if point_in_rect(event.pos, brect):
                                            g.brush_size = size
                                            palette_clicked = True
                                            break
                                # Check clear button
                                if not palette_clicked:
                                    clear_btn = pygame.Rect(20, CANVAS_Y + CANVAS_H + 60, 160, 35)
                                    if point_in_rect(event.pos, clear_btn):
                                        g.canvas.fill(WHITE)
                                        palette_clicked = True
                                # Start drawing on canvas
                                if not palette_clicked:
                                    canvas_rect = pygame.Rect(CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H)
                                    if point_in_rect(event.pos, canvas_rect):
                                        g.drawing = True
                                        g.last_pos = (mx - CANVAS_X, my - CANVAS_Y)
                                        pygame.draw.circle(g.canvas, g.draw_color, g.last_pos, g.brush_size // 2)
                        elif event.type == pygame.MOUSEBUTTONUP:
                            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                                g.drawing = False
                                g.last_pos = None
                        elif event.type == pygame.MOUSEMOTION:
                            if g.drawing:
                                canvas_rect = pygame.Rect(CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H)
                                if point_in_rect(event.pos, canvas_rect):
                                    cur_pos = (mx - CANVAS_X, my - CANVAS_Y)
                                    if g.last_pos:
                                        pygame.draw.line(g.canvas, g.draw_color, g.last_pos, cur_pos, g.brush_size)
                                        pygame.draw.circle(g.canvas, g.draw_color, cur_pos, g.brush_size // 2)
                                    g.last_pos = cur_pos
                                else:
                                    g.last_pos = None
                    # Drawing done button (player)
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                        g.drawing_done = True

                # When drawing is done, move to guessing phase
                if g.drawing_done and not g.guessing_started:
                    g.guessing_started = True
                    if g.drawer == "ai":
                        g.phase = "guessing"
                        g.guess_result = ""
                        g.guesses = []
                        g.result_timer = 0
                    else:
                        g.start_guessing()
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        # Check palette clicks
                        palette_clicked = False
                        for i, color in enumerate(PALETTE):
                            cx = 20 + (i % 2) * 40
                            cy = 80 + (i // 2) * 40
                            crect = pygame.Rect(cx, cy, 34, 34)
                            if point_in_rect(event.pos, crect):
                                g.draw_color = color
                                palette_clicked = True
                                break

                        # Check brush size clicks
                        if not palette_clicked:
                            for i, size in enumerate(BRUSH_SIZES):
                                bx = 20 + i * 34
                                by = CANVAS_Y + CANVAS_H + 20
                                brect = pygame.Rect(bx, by, 30, 30)
                                if point_in_rect(event.pos, brect):
                                    g.brush_size = size
                                    palette_clicked = True
                                    break

                        # Check clear button
                        if not palette_clicked:
                            clear_btn = pygame.Rect(20, CANVAS_Y + CANVAS_H + 60, 160, 35)
                            if point_in_rect(event.pos, clear_btn):
                                g.canvas.fill(WHITE)
                                palette_clicked = True

                        # Start drawing on canvas
                        if not palette_clicked:
                            canvas_rect = pygame.Rect(CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H)
                            if point_in_rect(event.pos, canvas_rect):
                                g.drawing = True
                                g.last_pos = (mx - CANVAS_X, my - CANVAS_Y)
                                pygame.draw.circle(g.canvas, g.draw_color, g.last_pos, g.brush_size // 2)

                elif event.type == pygame.MOUSEBUTTONUP:
                    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                        g.drawing = False
                        g.last_pos = None

                elif event.type == pygame.MOUSEMOTION:
                    if g.drawing:
                        canvas_rect = pygame.Rect(CANVAS_X, CANVAS_Y, CANVAS_W, CANVAS_H)
                        if point_in_rect(event.pos, canvas_rect):
                            cur_pos = (mx - CANVAS_X, my - CANVAS_Y)
                            if g.last_pos:
                                pygame.draw.line(g.canvas, g.draw_color, g.last_pos, cur_pos, g.brush_size)
                                pygame.draw.circle(g.canvas, g.draw_color, cur_pos, g.brush_size // 2)
                            g.last_pos = cur_pos
                        else:
                            g.last_pos = None

                elif event.type == pygame.KEYDOWN:
                    if g.guess_result == "correct":
                        pass  # ignore input after correct guess
                    elif event.key == pygame.K_RETURN:
                        guess = g.guess_text.strip().lower()
                        if guess:
                            if guess == g.word.lower():
                                g.guess_result = "correct"
                                # Score based on time left
                                bonus = max(10, int(g.time_left * 2))
                                g.score += bonus
                                g.guesses.append(("✓ " + g.guess_text, CORRECT_GREEN))
                                g.result_timer = time.time()
                            else:
                                g.guesses.append(("✗ " + g.guess_text, WRONG_RED))
                                # Give a hint every 3 wrong guesses
                                if len([gx for gx in g.guesses if gx[1] == WRONG_RED]) % 3 == 0:
                                    g.build_hint()
                            g.guess_text = ""
                    elif event.key == pygame.K_BACKSPACE:
                        g.guess_text = g.guess_text[:-1]
                    elif event.key == pygame.K_ESCAPE:
                        # Skip round
                        g.phase = "result"
                        g.guess_result = "skipped"
                        g.result_timer = time.time()
                    else:
                        if len(g.guess_text) < 25 and event.unicode.isprintable():
                            g.guess_text += event.unicode

            # ── GUESSING PHASE ──
            elif g.phase == "guessing":
                # If AI is the drawer, let it guess immediately and move to result
                if g.drawer == "ai" and g.guess_result == "":
                    g.guess_result = "correct"
                    g.result_timer = time.time()
                # Only the non-drawer can guess
                elif g.drawer == "player":
                    # AI guesses
                    if ai.active and g.guess_result == "" and (time.time() - ai.last_guess_time > ai.guess_interval):
                        ai.last_guess_time = time.time()
                        ai_guess = ai.make_guess(g.hint)
                        if ai_guess.lower() == g.word.lower():
                            g.guess_result = "correct"
                            bonus = max(10, int(g.time_left * 2))
                            g.score += bonus
                            g.guesses.append(("AI ✓ " + ai_guess, CORRECT_GREEN))
                            g.result_timer = time.time()
                            g.phase = "result"
                        else:
                            g.guesses.append(("AI ✗ " + ai_guess, WRONG_RED))
                            # Give a hint every 3 wrong guesses
                            if len([gx for gx in g.guesses if gx[1] == WRONG_RED]) % 3 == 0:
                                g.build_hint()
                else:
                    # Player guesses
                    if event.type == pygame.KEYDOWN:
                        if g.guess_result == "correct":
                            pass  # ignore input after correct guess
                        elif event.key == pygame.K_RETURN:
                            guess = g.guess_text.strip().lower()
                            if guess:
                                if guess == g.word.lower():
                                    g.guess_result = "correct"
                                    # Score based on time left
                                    bonus = max(10, int(g.time_left * 2))
                                    g.score += bonus
                                    g.guesses.append(("✓ " + g.guess_text, CORRECT_GREEN))
                                    g.result_timer = time.time()
                                else:
                                    g.guesses.append(("✗ " + g.guess_text, WRONG_RED))
                                    # Give a hint every 3 wrong guesses
                                    if len([gx for gx in g.guesses if gx[1] == WRONG_RED]) % 3 == 0:
                                        g.build_hint()
                                g.guess_text = ""
                        elif event.key == pygame.K_BACKSPACE:
                            g.guess_text = g.guess_text[:-1]
                        elif event.key == pygame.K_ESCAPE:
                            # Skip round
                            g.phase = "result"
                            g.guess_result = "skipped"
                            g.result_timer = time.time()
                        else:
                            if len(g.guess_text) < 25 and event.unicode.isprintable():
                                g.guess_text += event.unicode

            # ── RESULT ──
            elif g.phase == "result":
                if event.type == pygame.MOUSEBUTTONDOWN or event.type == pygame.KEYDOWN:
                    if time.time() - g.result_timer > 1.0:
                        if g.current_round >= TOTAL_ROUNDS:
                            g.phase = "game_over"
                        else:
                            g.new_round()

            # ── GAME OVER ──
            elif g.phase == "game_over":
                if event.type == pygame.MOUSEBUTTONDOWN:
                    play_btn = pygame.Rect(SCREEN_WIDTH // 2 - 120, 480, 240, 55)
                    if point_in_rect(event.pos, play_btn):
                        g.__init__()
                        g.new_round()

        # ── Time logic during drawing ──
        if g.phase == "drawing" and g.guess_result != "correct":
            g.time_left = max(0, ROUND_TIME - (time.time() - g.round_start))
            # Auto-hint at certain times
            if g.time_left < ROUND_TIME * 0.5 and g.hints_given == 0:
                g.build_hint()
            if g.time_left < ROUND_TIME * 0.25 and g.hints_given <= 1:
                g.build_hint()
            if g.time_left <= 0:
                g.phase = "result"
                g.guess_result = "timeout"
                g.result_timer = time.time()

        # Move to result after correct guess delay
        if g.phase == "drawing" and g.guess_result == "correct":
            if time.time() - g.result_timer > 2.0:
                if g.current_round >= TOTAL_ROUNDS:
                    g.phase = "game_over"
                else:
                    g.new_round()

        # --- Prevent AI guessing phase from freezing the game ---
        if g.phase == "guessing" and g.drawer == "ai":
            # Automatically move to result after a short delay
            if not hasattr(g, 'ai_guessing_start_time'):
                g.ai_guessing_start_time = time.time()
            if time.time() - g.ai_guessing_start_time > 2.0:
                g.phase = "result"
                g.guess_result = "ai_done"
                g.result_timer = time.time()
                del g.ai_guessing_start_time
        else:
            # Always clean up ai_guessing_start_time if not in AI guessing phase
            if hasattr(g, 'ai_guessing_start_time'):
                del g.ai_guessing_start_time

        # ══════════════════ DRAWING ══════════════════
        screen.fill(BG_COLOR)

        # --- Render everything to a virtual surface ---
        virtual_surface = pygame.Surface((SCREEN_WIDTH - PAGE_SCROLLBAR_W, VIRTUAL_PAGE_HEIGHT))
        virtual_surface.fill(BG_COLOR)

        # Only disable drawing and guess history mouse UI during AI guessing phase
        drawing_mouse_ui_enabled = True
        guess_history_mouse_ui_enabled = True
        if g.phase == "guessing" and g.drawer == "ai":
            drawing_mouse_ui_enabled = False
            guess_history_mouse_ui_enabled = False

        # Replace all 'screen' with 'virtual_surface' below for drawing
        draw_target = virtual_surface

        # ── MENU ──
        # Draw fullscreen toggle button (top right)
        fs_btn_rect = pygame.Rect(SCREEN_WIDTH - PAGE_SCROLLBAR_W - 160, 10, 140, 38)
        draw_button(draw_target, "Toggle Fullscreen (F11)", fs_btn_rect, ACCENT, WHITE, font_small)
        # Handle mouse click on fullscreen button
        # Fullscreen button should always be enabled
        if pygame.mouse.get_pressed()[0]:
            if fs_btn_rect.collidepoint(mx, my + page_scroll_offset):
                pygame.time.wait(200)  # debounce
                fullscreen = not fullscreen
                set_screen(fullscreen)

        # --- All other drawing below should use draw_target instead of screen ---
        # (for brevity, not replacing every instance here, but you should replace all 'screen' with 'draw_target' in the rest of the code)

        if g.phase == "menu":
            title = font_big.render("Skribbl — Draw & Guess!", True, PANEL_COLOR)
            draw_target.blit(title, title.get_rect(center=(SCREEN_WIDTH // 2, 200)))

            info = font_med.render("One player draws, the other guesses!", True, DARK_GRAY)
            draw_target.blit(info, info.get_rect(center=(SCREEN_WIDTH // 2, 270)))

            rules = font_small.render(f"{TOTAL_ROUNDS} rounds  •  {ROUND_TIME}s per round  •  Type your guess & press Enter", True, DARK_GRAY)
            draw_target.blit(rules, rules.get_rect(center=(SCREEN_WIDTH // 2, 320)))

            play_btn = pygame.Rect(SCREEN_WIDTH // 2 - 120, 400, 240, 55)
            draw_button(draw_target, "PLAY", play_btn, ACCENT)

        # ── SHOW WORD (drawer sees the word) ──
        elif g.phase == "show_word":
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            draw_target.blit(overlay, (0, 0))

            box = pygame.Rect(SCREEN_WIDTH // 2 - 250, SCREEN_HEIGHT // 2 - 100, 500, 200)
            draw_rounded_rect(draw_target, WHITE, box, 16)

            t1 = font_med.render(f"Round {g.current_round} / {TOTAL_ROUNDS}", True, DARK_GRAY)
            draw_target.blit(t1, t1.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 60)))

            if g.drawer == "player":
                t2 = font_med.render("Your word to draw:", True, DARK_GRAY)
                draw_target.blit(t2, t2.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 20)))
                t3 = font_big.render(g.word.upper(), True, ACCENT)
                draw_target.blit(t3, t3.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 30)))
                t4 = font_small.render("Click or press any key to start drawing", True, GRAY)
                draw_target.blit(t4, t4.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 80)))
            else:
                t2 = font_med.render("AI is drawing this round!", True, DARK_GRAY)
                draw_target.blit(t2, t2.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)))
                t3 = font_small.render("Click or press any key to continue", True, GRAY)
                draw_target.blit(t3, t3.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 50)))

        # ── DRAWING PHASE ──
        elif g.phase == "drawing":
            # Show who is drawing and the word if player is the drawer
            if g.drawer == "ai":
                # Draw 'AI is drawing...' label above the canvas
                ai_label = font_big.render("AI is drawing...", True, ACCENT)
                draw_target.blit(ai_label, (CANVAS_X + CANVAS_W//2 - ai_label.get_width()//2, CANVAS_Y - 60))
                # Gradual AI drawing: draw a little each frame
                if not hasattr(g, 'ai_draw_steps') or g.ai_draw_steps is None:
                    g.canvas.fill(WHITE)
                    g.ai_draw_steps = []
                    for _ in range(12):
                        color = random.choice(PALETTE)
                        x1 = random.randint(0, CANVAS_W)
                        y1 = random.randint(0, CANVAS_H)
                        x2 = random.randint(0, CANVAS_W)
                        y2 = random.randint(0, CANVAS_H)
                        size = random.choice(BRUSH_SIZES)
                        g.ai_draw_steps.append(('line', color, x1, y1, x2, y2, size))
                    for _ in range(4):
                        color = random.choice(PALETTE)
                        x = random.randint(0, CANVAS_W-40)
                        y = random.randint(0, CANVAS_H-40)
                        w = random.randint(20, 80)
                        h = random.randint(20, 80)
                        g.ai_draw_steps.append(('ellipse', color, x, y, w, h))
                    g.ai_draw_step_idx = 0
                if g.ai_draw_step_idx < len(g.ai_draw_steps):
                    step = g.ai_draw_steps[g.ai_draw_step_idx]
                    if step[0] == 'line':
                        _, color, x1, y1, x2, y2, size = step
                        pygame.draw.line(g.canvas, color, (x1, y1), (x2, y2), size)
                    elif step[0] == 'ellipse':
                        _, color, x, y, w, h = step
                        pygame.draw.ellipse(g.canvas, color, (x, y, w, h), 0)
                    g.ai_draw_step_idx += 1
                # Player guesses (rest of drawing phase logic remains, but player can only guess)
                if g.ai_draw_step_idx >= len(g.ai_draw_steps):
                    if not hasattr(g, 'ai_drawn_time') or g.ai_drawn_time is None:
                        g.ai_drawn_time = time.time()
                    g.ai_drawn = True
                    # Show the completed drawing for 2 seconds before moving to guessing
                    if time.time() - g.ai_drawn_time > 2.0 and g.guess_result == "":
                        g.guess_result = "ai_drawn_done"
                else:
                    g.ai_drawn = False
                    g.ai_drawn_time = None
            else:
                # Show the word to the player above the canvas
                # Only show the word to the player, not to the AI
                if g.drawer == "player":
                    word_label = font_big.render(f"Your word: {g.word.upper()}", True, ACCENT)
                    draw_target.blit(word_label, (CANVAS_X + CANVAS_W//2 - word_label.get_width()//2, CANVAS_Y - 60))
                g.ai_drawn = False
            # else removed: no action needed here
            # Left panel
            panel_rect = pygame.Rect(0, 0, 190, SCREEN_HEIGHT)
            draw_rounded_rect(draw_target, PANEL_COLOR, panel_rect, 0)

            # Round & Score
            r_txt = font_small.render(f"Round {g.current_round}/{TOTAL_ROUNDS}", True, WHITE)
            draw_target.blit(r_txt, (15, 10))
            s_txt = font_small.render(f"Score: {g.score}", True, ACCENT)
            draw_target.blit(s_txt, (15, 35))

            # Palette label
            p_label = font_tiny.render("COLORS", True, GRAY)
            draw_target.blit(p_label, (15, 63))

            # Color palette
            for i, color in enumerate(PALETTE):
                cx = 20 + (i % 2) * 40
                cy = 80 + (i // 2) * 40
                crect = pygame.Rect(cx, cy, 34, 34)
                pygame.draw.rect(draw_target, color, crect, border_radius=4)
                if color == g.draw_color:
                    pygame.draw.rect(draw_target, WHITE, crect, 3, border_radius=4)
                else:
                    pygame.draw.rect(draw_target, DARK_GRAY, crect, 1, border_radius=4)

            # Brush sizes
            bs_label = font_tiny.render("BRUSH SIZE", True, GRAY)
            draw_target.blit(bs_label, (15, CANVAS_Y + CANVAS_H + 3))
            for i, size in enumerate(BRUSH_SIZES):
                bx = 20 + i * 34
                by = CANVAS_Y + CANVAS_H + 20
                brect = pygame.Rect(bx, by, 30, 30)
                draw_rounded_rect(draw_target, (70, 75, 90) if size != g.brush_size else ACCENT, brect, 6)
                pygame.draw.circle(draw_target, WHITE, brect.center, min(size // 2, 12))

            # Clear button
            clear_btn = pygame.Rect(20, CANVAS_Y + CANVAS_H + 60, 160, 35)
            draw_button(draw_target, "Clear Canvas", clear_btn, WRONG_RED, WHITE, font_small)

            # Canvas border + surface
            canvas_border = pygame.Rect(CANVAS_X - 2, CANVAS_Y - 2, CANVAS_W + 4, CANVAS_H + 4)
            pygame.draw.rect(draw_target, DARK_GRAY, canvas_border, 2, border_radius=4)
            draw_target.blit(g.canvas, (CANVAS_X, CANVAS_Y))

            # Timer bar
            timer_ratio = g.time_left / ROUND_TIME
            bar_rect = pygame.Rect(CANVAS_X, CANVAS_Y - 22, CANVAS_W, 16)
            pygame.draw.rect(draw_target, GRAY, bar_rect, border_radius=4)
            fill_color = CORRECT_GREEN if timer_ratio > 0.3 else (255, 180, 0) if timer_ratio > 0.1 else WRONG_RED
            fill_rect = pygame.Rect(CANVAS_X, CANVAS_Y - 22, int(CANVAS_W * timer_ratio), 16)
            pygame.draw.rect(draw_target, fill_color, fill_rect, border_radius=4)
            timer_txt = font_tiny.render(f"{int(g.time_left)}s", True, BLACK)
            draw_target.blit(timer_txt, (CANVAS_X + CANVAS_W - 30, CANVAS_Y - 23))

            # Hint
            hint_txt = font_med.render(f"Hint: {g.hint}", True, DARK_GRAY)
            draw_target.blit(hint_txt, (CANVAS_X, CANVAS_Y + CANVAS_H + 10))

            # Guess input box
            input_rect = pygame.Rect(CANVAS_X, CANVAS_Y + CANVAS_H + 45, CANVAS_W - 100, 36)
            pygame.draw.rect(draw_target, WHITE, input_rect, border_radius=6)
            pygame.draw.rect(draw_target, ACCENT if g.guess_result == "" else CORRECT_GREEN, input_rect, 2, border_radius=6)
            inp_txt = font_input.render(g.guess_text + "│", True, BLACK)
            draw_target.blit(inp_txt, (input_rect.x + 10, input_rect.y + 7))

            # Enter label
            enter_label = font_small.render("Enter ↵", True, DARK_GRAY)
            draw_target.blit(enter_label, (input_rect.right + 10, input_rect.y + 7))


            # Scrollable guess history with vertical and horizontal scrollbars
            # --- Scrollbar constants ---
            GH_X = CANVAS_X
            GH_Y = CANVAS_Y + CANVAS_H + 88
            GH_W = CANVAS_W - 30  # leave space for vertical scrollbar
            GH_H = 6 * 22  # show 6 guesses at a time
            VSCROLLBAR_W = 18
            HSCROLLBAR_H = 16
            total_guesses = len(g.guesses)
            max_visible = 6
            scroll_area_rect = pygame.Rect(GH_X, GH_Y, GH_W, GH_H)
            vscrollbar_rect = pygame.Rect(GH_X + GH_W + 4, GH_Y, VSCROLLBAR_W, GH_H)
            hscrollbar_rect = pygame.Rect(GH_X, GH_Y + GH_H + 4, GH_W, HSCROLLBAR_H)

            # Track scroll offsets in Game object
            if not hasattr(g, 'guess_scroll_offset'):
                g.guess_scroll_offset = 0
            if not hasattr(g, 'guess_hscroll_offset'):
                g.guess_hscroll_offset = 0
            max_scroll = max(0, total_guesses - max_visible)

            # Find max guess width
            max_guess_width = 0
            for gtxt, _ in g.guesses:
                gt = font_small.render(gtxt, True, BLACK)
                max_guess_width = max(max_guess_width, gt.get_width())
            max_hscroll = max(0, max_guess_width - GH_W)
            # Clamp scroll offsets
            g.guess_scroll_offset = max(0, min(g.guess_scroll_offset, max_scroll))
            g.guess_hscroll_offset = max(0, min(g.guess_hscroll_offset, max_hscroll))

            # Draw guess history area (clip to area)
            history_surface = pygame.Surface((GH_W, GH_H))
            history_surface.fill(WHITE)
            for i in range(max_visible):
                idx = i + g.guess_scroll_offset
                if idx < total_guesses:
                    gtxt, gcol = g.guesses[idx]
                    gt = font_small.render(gtxt, True, gcol)
                    history_surface.blit(gt, (-g.guess_hscroll_offset, i * 22))
            draw_target.blit(history_surface, (GH_X, GH_Y))

            # Draw vertical scrollbar background
            pygame.draw.rect(draw_target, LIGHT_GRAY, vscrollbar_rect, border_radius=8)
            # Draw vertical scrollbar handle
            if total_guesses > max_visible:
                handle_h = max(24, int(GH_H * (max_visible / total_guesses)))
                handle_y = GH_Y + int((GH_H - handle_h) * (g.guess_scroll_offset / max_scroll)) if max_scroll > 0 else GH_Y
                vhandle_rect = pygame.Rect(vscrollbar_rect.x + 2, handle_y, VSCROLLBAR_W - 4, handle_h)
                pygame.draw.rect(draw_target, ACCENT, vhandle_rect, border_radius=8)

            # Draw horizontal scrollbar background
            pygame.draw.rect(draw_target, LIGHT_GRAY, hscrollbar_rect, border_radius=8)
            # Draw horizontal scrollbar handle
            if max_guess_width > GH_W:
                hhandle_w = max(24, int(GH_W * (GH_W / max_guess_width)))
                hhandle_x = GH_X + int((GH_W - hhandle_w) * (g.guess_hscroll_offset / max_hscroll)) if max_hscroll > 0 else GH_X
                hhandle_rect = pygame.Rect(hhandle_x, hscrollbar_rect.y + 2, hhandle_w, HSCROLLBAR_H - 4)
                pygame.draw.rect(draw_target, ACCENT, hhandle_rect, border_radius=8)

            # Handle mouse wheel for scrolling
            if guess_history_mouse_ui_enabled:
                for event in pygame.event.get(pygame.MOUSEWHEEL):
                    if g.phase == "drawing":
                        if scroll_area_rect.collidepoint(pygame.mouse.get_pos()) or vscrollbar_rect.collidepoint(pygame.mouse.get_pos()):
                            g.guess_scroll_offset -= event.y  # up is positive, down is negative
                            g.guess_scroll_offset = max(0, min(g.guess_scroll_offset, max_scroll))
                        elif hscrollbar_rect.collidepoint(pygame.mouse.get_pos()):
                            g.guess_hscroll_offset -= event.y * 20  # horizontal scroll with wheel
                            g.guess_hscroll_offset = max(0, min(g.guess_hscroll_offset, max_hscroll))

            # Handle vertical scrollbar dragging
            if not hasattr(g, 'vscrollbar_dragging'):
                g.vscrollbar_dragging = False
                g.vscrollbar_drag_offset = 0
            mouse_x, mouse_y = pygame.mouse.get_pos()
            mouse_pressed = pygame.mouse.get_pressed()[0]
            if guess_history_mouse_ui_enabled:
                if mouse_pressed and total_guesses > max_visible:
                    if not g.vscrollbar_dragging:
                        if 'vhandle_rect' in locals() and vhandle_rect.collidepoint((mouse_x, mouse_y)):
                            g.vscrollbar_dragging = True
                            g.vscrollbar_drag_offset = mouse_y - vhandle_rect.y
                    else:
                        rel_y = mouse_y - GH_Y - g.vscrollbar_drag_offset
                        rel_y = max(0, min(rel_y, GH_H - handle_h))
                        g.guess_scroll_offset = int((rel_y / (GH_H - handle_h)) * max_scroll) if (GH_H - handle_h) > 0 else 0
                else:
                    g.vscrollbar_dragging = False

                # Handle horizontal scrollbar dragging
                if not hasattr(g, 'hscrollbar_dragging'):
                    g.hscrollbar_dragging = False
                    g.hscrollbar_drag_offset = 0
                if mouse_pressed and max_guess_width > GH_W:
                    if not g.hscrollbar_dragging:
                        if 'hhandle_rect' in locals() and hhandle_rect.collidepoint((mouse_x, mouse_y)):
                            g.hscrollbar_dragging = True
                            g.hscrollbar_drag_offset = mouse_x - hhandle_rect.x
                    else:
                        rel_x = mouse_x - GH_X - g.hscrollbar_drag_offset
                        rel_x = max(0, min(rel_x, GH_W - hhandle_w))
                        g.guess_hscroll_offset = int((rel_x / (GH_W - hhandle_w)) * max_hscroll) if (GH_W - hhandle_w) > 0 else 0
                else:
                    g.hscrollbar_dragging = False

            # Correct overlay
            if g.guess_result == "correct":
                overlay = pygame.Surface((CANVAS_W, CANVAS_H), pygame.SRCALPHA)
                overlay.fill((40, 200, 80, 60))
                draw_target.blit(overlay, (CANVAS_X, CANVAS_Y))
                correct_txt = font_big.render("CORRECT!", True, CORRECT_GREEN)
                draw_target.blit(correct_txt, correct_txt.get_rect(center=(CANVAS_X + CANVAS_W // 2, CANVAS_Y + CANVAS_H // 2)))

        # ── RESULT ──
        elif g.phase == "result":
            box = pygame.Rect(SCREEN_WIDTH // 2 - 250, SCREEN_HEIGHT // 2 - 120, 500, 240)
            draw_rounded_rect(draw_target, WHITE, box, 16)
            pygame.draw.rect(draw_target, DARK_GRAY, box, 2, border_radius=16)

            if g.guess_result == "timeout":
                t1 = font_big.render("Time's Up!", True, WRONG_RED)
            else:
                t1 = font_big.render("Round Skipped", True, DARK_GRAY)
            draw_target.blit(t1, t1.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 70)))

            t2 = font_med.render(f'The word was: "{g.word.upper()}"', True, DARK_GRAY)
            draw_target.blit(t2, t2.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 15)))

            t3 = font_small.render(f"Score: {g.score}", True, ACCENT)
            draw_target.blit(t3, t3.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 30)))

            t4 = font_small.render("Click or press any key to continue", True, GRAY)
            draw_target.blit(t4, t4.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 80)))

        # ── GAME OVER ──
        elif g.phase == "game_over":
            t1 = font_big.render("Game Over!", True, PANEL_COLOR)
            draw_target.blit(t1, t1.get_rect(center=(SCREEN_WIDTH // 2, 220)))

            t2 = font_big.render(f"Final Score: {g.score}", True, ACCENT)
            draw_target.blit(t2, t2.get_rect(center=(SCREEN_WIDTH // 2, 300)))

            grade = "Amazing!" if g.score > 500 else "Great!" if g.score > 300 else "Good job!" if g.score > 100 else "Keep practicing!"
            t3 = font_med.render(grade, True, CORRECT_GREEN if g.score > 300 else DARK_GRAY)
            draw_target.blit(t3, t3.get_rect(center=(SCREEN_WIDTH // 2, 370)))

            play_btn = pygame.Rect(SCREEN_WIDTH // 2 - 120, 440, 240, 55)
            draw_button(draw_target, "PLAY AGAIN", play_btn, ACCENT)

        # --- Blit the visible part of the virtual surface to the screen ---
        screen.blit(virtual_surface, (0, 0), area=pygame.Rect(0, page_scroll_offset, SCREEN_WIDTH - PAGE_SCROLLBAR_W, SCREEN_HEIGHT))

        # --- Draw the page scrollbar ---
        max_page_scroll = max(0, VIRTUAL_PAGE_HEIGHT - SCREEN_HEIGHT)
        scrollbar_rect = pygame.Rect(SCREEN_WIDTH - PAGE_SCROLLBAR_W, 0, PAGE_SCROLLBAR_W, SCREEN_HEIGHT)
        pygame.draw.rect(screen, LIGHT_GRAY, scrollbar_rect, border_radius=8)
        if max_page_scroll > 0:
            handle_h = max(40, int(SCREEN_HEIGHT * (SCREEN_HEIGHT / VIRTUAL_PAGE_HEIGHT)))
            handle_y = int((SCREEN_HEIGHT - handle_h) * (page_scroll_offset / max_page_scroll)) if max_page_scroll > 0 else 0
            handle_rect = pygame.Rect(SCREEN_WIDTH - PAGE_SCROLLBAR_W + 2, handle_y, PAGE_SCROLLBAR_W - 4, handle_h)
            pygame.draw.rect(screen, ACCENT, handle_rect, border_radius=8)
            # Handle scrollbar dragging
            mouse_x, mouse_y = pygame.mouse.get_pos()
            mouse_pressed = pygame.mouse.get_pressed()[0]
            # Page scrollbar should always be enabled
            if not hasattr(g, 'page_scrollbar_dragging'):
                g.page_scrollbar_dragging = False
                g.page_scrollbar_drag_offset = 0
            if mouse_pressed:
                if not g.page_scrollbar_dragging:
                    if handle_rect.collidepoint(mouse_x, mouse_y):
                        g.page_scrollbar_dragging = True
                        g.page_scrollbar_drag_offset = mouse_y - handle_rect.y
                else:
                    rel_y = mouse_y - g.page_scrollbar_drag_offset
                    rel_y = max(0, min(rel_y, SCREEN_HEIGHT - handle_h))
                    page_scroll_offset = int((rel_y / (SCREEN_HEIGHT - handle_h)) * max_page_scroll) if (SCREEN_HEIGHT - handle_h) > 0 else 0
            else:
                g.page_scrollbar_dragging = False

        # Blit the virtual surface to the main screen before flipping
        screen.blit(virtual_surface, (0, -page_scroll_offset))
        pygame.display.flip()

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
