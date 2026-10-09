\set ON_ERROR_STOP on
BEGIN;
CREATE TABLE authors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (length(trim(name)) > 0),
    country text NOT NULL
);
CREATE TABLE books (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    author_id bigint NOT NULL REFERENCES authors(id),
    title text NOT NULL CHECK (length(trim(title)) > 0),
    isbn text NOT NULL UNIQUE,
    price numeric(10,2) NOT NULL CHECK (price >= 0),
    stock integer NOT NULL DEFAULT 0 CHECK (stock >= 0)
);
CREATE TABLE customers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (length(trim(name)) > 0),
    email text NOT NULL UNIQUE,
    phone text
);
CREATE TABLE orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers(id),
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'paid', 'shipped', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE order_items (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    book_id bigint NOT NULL REFERENCES books(id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(10,2) NOT NULL CHECK (unit_price >= 0),
    UNIQUE (order_id, book_id)
);
CREATE INDEX ON books(author_id);
CREATE INDEX ON orders(customer_id);
CREATE INDEX ON order_items(book_id);
COMMIT;
