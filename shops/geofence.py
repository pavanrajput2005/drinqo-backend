from math import asin, cos, radians, sin, sqrt


EARTH_RADIUS_METERS = 6_371_000


def distance_between_coordinates(
    latitude_a,
    longitude_a,
    latitude_b,
    longitude_b,
):
    latitude_delta = radians(float(latitude_b) - float(latitude_a))
    longitude_delta = radians(float(longitude_b) - float(longitude_a))
    latitude_a_radians = radians(float(latitude_a))
    latitude_b_radians = radians(float(latitude_b))

    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(latitude_a_radians)
        * cos(latitude_b_radians)
        * sin(longitude_delta / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(haversine))
