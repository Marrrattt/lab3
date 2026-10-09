\set ON_ERROR_STOP on
BEGIN;
INSERT INTO authors (name, country) VALUES
    ('Михаил Булгаков', 'Россия'), ('Джордж Оруэлл', 'Великобритания'), ('Жюль Верн', 'Франция');
INSERT INTO books (author_id, title, isbn, price, stock) VALUES
    (1, 'Мастер и Маргарита', '978-5-17-000001-1', 750.00, 10),
    (1, 'Собачье сердце', '978-5-17-000002-8', 450.00, 15),
    (2, '1984', '978-5-17-000003-5', 600.00, 12),
    (3, 'Таинственный остров', '978-5-17-000004-2', 550.00, 8);
INSERT INTO customers (name, email, phone) VALUES
    ('Анна', 'anna@example.test', '+70000000001'),
    ('Иван', 'ivan@example.test', NULL);
INSERT INTO orders (customer_id, status) VALUES (1, 'new'), (2, 'paid');
INSERT INTO order_items (order_id, book_id, quantity, unit_price) VALUES
    (1, 1, 1, 750.00), (1, 3, 2, 600.00), (2, 2, 1, 450.00);
COMMIT;
