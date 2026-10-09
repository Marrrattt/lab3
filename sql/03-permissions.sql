\set ON_ERROR_STOP on
\getenv app_password APP_PASSWORD
CREATE ROLE bookstore_user LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE bookstore FROM PUBLIC;
GRANT CONNECT ON DATABASE bookstore TO bookstore_user;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO bookstore_user;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO bookstore_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bookstore_user;
-- UPDATE is granted per column: changing id is forbidden even outside the app.
GRANT UPDATE (name, country) ON authors TO bookstore_user;
GRANT UPDATE (author_id, title, isbn, price, stock) ON books TO bookstore_user;
GRANT UPDATE (name, email, phone) ON customers TO bookstore_user;
GRANT UPDATE (customer_id, status, created_at) ON orders TO bookstore_user;
GRANT UPDATE (order_id, book_id, quantity, unit_price) ON order_items TO bookstore_user;
