CREATE TABLE `word_lists` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(255) NOT NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`)
);

CREATE TABLE `words` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `text` VARCHAR(255) NOT NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`)
);

CREATE TABLE `word_list_entries` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `word_list_id` INT NOT NULL,
    `word_id` INT NOT NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `uq_word_list_entries_word_list_word` UNIQUE (`word_list_id`, `word_id`),
    CONSTRAINT `fk_word_list_entries_word_list`
        FOREIGN KEY (`word_list_id`) REFERENCES `word_lists` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_word_list_entries_word`
        FOREIGN KEY (`word_id`) REFERENCES `words` (`id`) ON DELETE CASCADE
);

CREATE TABLE `rooms` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(255) NOT NULL,
    `join_code` CHAR(8) NOT NULL,
    `visibility` ENUM('public', 'private') NOT NULL,
    `status` ENUM('lobby', 'in_progress', 'empty_grace') NOT NULL,
    `max_players` INT NOT NULL DEFAULT 6,
    `selected_word_list_id` INT NOT NULL,
    `host_participant_id` INT NULL,
    `empty_since` DATETIME NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `uq_rooms_join_code` UNIQUE (`join_code`),
    CONSTRAINT `fk_rooms_selected_word_list`
        FOREIGN KEY (`selected_word_list_id`) REFERENCES `word_lists` (`id`) ON DELETE RESTRICT
);

CREATE TABLE `room_participants` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `room_id` INT NOT NULL,
    `session_key` VARCHAR(64) NOT NULL,
    `nickname` VARCHAR(24) NOT NULL,
    `connection_status` ENUM('connected', 'disconnected') NOT NULL,
    `participation_status` ENUM('playing', 'spectating') NOT NULL,
    `current_score` INT NOT NULL DEFAULT 0,
    `last_seen_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `uq_room_participants_room_session` UNIQUE (`room_id`, `session_key`),
    CONSTRAINT `fk_room_participants_room`
        FOREIGN KEY (`room_id`) REFERENCES `rooms` (`id`) ON DELETE CASCADE
);

ALTER TABLE `rooms`
    ADD CONSTRAINT `fk_rooms_host_participant`
        FOREIGN KEY (`host_participant_id`) REFERENCES `room_participants` (`id`) ON DELETE SET NULL;

CREATE TABLE `games` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `room_id` INT NOT NULL,
    `status` ENUM('in_progress', 'finished', 'cancelled') NOT NULL,
    `started_at` DATETIME NOT NULL,
    `ended_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_games_room`
        FOREIGN KEY (`room_id`) REFERENCES `rooms` (`id`) ON DELETE CASCADE
);

CREATE TABLE `game_words` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `game_id` INT NOT NULL,
    `text` VARCHAR(255) NOT NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `uq_game_words_game_text` UNIQUE (`game_id`, `text`),
    CONSTRAINT `fk_game_words_game`
        FOREIGN KEY (`game_id`) REFERENCES `games` (`id`) ON DELETE CASCADE
);

CREATE TABLE `turns` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `game_id` INT NOT NULL,
    `drawer_participant_id` INT NULL,
    `drawer_nickname` VARCHAR(24) NOT NULL,
    `selected_game_word_id` INT NOT NULL,
    `sequence_number` INT NOT NULL,
    `status` ENUM('completed', 'drawer_disconnected', 'cancelled') NOT NULL,
    `started_at` DATETIME NOT NULL,
    `ended_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL,
    `updated_at` DATETIME NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `uq_turns_game_sequence` UNIQUE (`game_id`, `sequence_number`),
    CONSTRAINT `uq_turns_selected_game_word` UNIQUE (`selected_game_word_id`),
    CONSTRAINT `fk_turns_game`
        FOREIGN KEY (`game_id`) REFERENCES `games` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_turns_drawer_participant`
        FOREIGN KEY (`drawer_participant_id`) REFERENCES `room_participants` (`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_turns_selected_game_word`
        FOREIGN KEY (`selected_game_word_id`) REFERENCES `game_words` (`id`) ON DELETE RESTRICT
);
